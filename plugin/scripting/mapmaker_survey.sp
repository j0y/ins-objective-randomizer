/**
 * mapmaker_survey.sp — stage A of docs/layout-variants.md: dump engine state
 * to JSON and move on.
 *
 * This is not gameplay code. It is a data exporter, and it exists because
 * three things the layout generator needs cannot be had from the map files:
 *
 *   1. The nav connection graph. The offline .nav parser truncates
 *      (ministry_coop: EOF at area 3102/3128, graph in components of
 *      2379/533/178, no path from the security spawn to objectives 2-6).
 *      Here the graph is the engine's own, with the engine's own edge lengths.
 *   2. Hull validity. CINSRules::IsSpawnPointValid is a player-hull fit
 *      against the real collision world - func_detail and static props
 *      included. Three offline predictors failed against it; TR_TraceHull is
 *      the same test, in-process.
 *   3. A mesh at all, for the five workshop maps that ship no .nav.
 *
 * Run once per map version. After that the analysis never needs a server.
 *
 *   sm_survey            dump the current map now
 *   mm_survey_auto 1     dump on map start, then changelevel to the next
 *                        entry of the rotation
 *   mm_survey_list_file  the rotation, one map per line. A cvar cannot hold a
 *                        real batch - see ReadRotation.
 */

#pragma semicolon 1
#pragma newdecls required

#include <sourcemod>
#include <sdktools>
#include "include/mm_nav.inc"

// Six of the 34 subscribed workshop maps enumerated *exactly* 16384 areas -
// the old cap - and said nothing about it, which is a truncated graph wearing
// a complete graph's clothes. The offline .nav parser truncating is the whole
// reason this plugin exists; the plugin must not do it too. The cap is now
// well clear of the largest mesh seen (gioconda_eron_garbage), and hitting it
// is loud, in the log and in the JSON.
#define MM_MAX_AREAS 65536

public Plugin myinfo =
{
	name        = "mapmaker survey",
	author      = "j0y",
	description = "Export nav graph, hull validity and objective entities to JSON",
	version     = "0.1.0",
	url         = ""
};

static ConVar g_cvOut;
static ConVar g_cvAuto;
static ConVar g_cvList;
static ConVar g_cvListFile;
static ConVar g_cvQuit;
static ConVar g_cvVis;
static ConVar g_cvHullMin;
static ConVar g_cvHullMax;
static ConVar g_cvDelay;

static Address g_areas[MM_MAX_AREAS];
static int     g_areaCount;
static bool    g_areaOverflow;
static StringMap g_areaIndex;
static bool    g_navReady;

// One survey per map load, however many times the arming hooks fire.
// The guard is a token rather than a timer handle: the hooks arm out of order
// and more than once, and a handle that has been re-armed, already fired, or
// killed by a map change is three different lifetimes to get right. A stale
// timer that finds a stale token simply does nothing.
static bool g_surveyed;
static int  g_armToken;

// Entity classes the generator has to know about. ins_spawnpoint is by far
// the most numerous (442 on ministry_coop) and is what binds to a zone by
// containment, so it has to be exported point by point.
static const char g_classes[][] = {
	"ins_spawnzone",
	"ins_blockzone",
	"ins_spawnpoint",
	"trigger_capture_zone",
	"point_controlpoint",
	"obj_weapon_cache",
	"ins_objective",
};

// ===========================================================================
// Lifecycle
// ===========================================================================

public void OnPluginStart()
{
	g_navReady = MM_NavInit("survey");
	if (!g_navReady)
		SetFailState("[survey] nav init failed - see errors above");

	g_cvOut = CreateConVar("mm_survey_out", "surveys",
		"Directory under the game dir to write <map>.json into");
	g_cvAuto = CreateConVar("mm_survey_auto", "0",
		"Dump on map start, then changelevel to the next map in mm_survey_list");
	g_cvList = CreateConVar("mm_survey_list", "",
		"Space-separated map rotation for mm_survey_auto. A console command "
		... "cannot carry more than 512 bytes, so this only holds a short "
		... "rotation - use mm_survey_list_file for a real batch.");
	g_cvListFile = CreateConVar("mm_survey_list_file", "",
		"Path under the game dir to a rotation file, one map per line. Takes "
		... "precedence over mm_survey_list when it names a file that opens.");
	g_cvQuit = CreateConVar("mm_survey_quit", "0",
		"Shut the server down when the rotation finishes, so an unattended "
		... "batch ends by itself instead of idling until something kills it.");
	g_cvVis = CreateConVar("mm_survey_vis", "0",
		"Also export the area-pair visibility matrix. O(n^2) SDKCalls - on a "
		... "3,100-area map that is ~9.6M calls and will stall the server for "
		... "minutes. Off until the cost is measured.");
	g_cvHullMin = CreateConVar("mm_survey_hull_min", "-16 -16 0",
		"Player hull mins for the validity trace");
	g_cvHullMax = CreateConVar("mm_survey_hull_max", "16 16 72",
		"Player hull maxs for the validity trace");
	g_cvDelay = CreateConVar("mm_survey_delay", "10.0",
		"Seconds after map start before dumping. The mesh is decorated in "
		... "CINSNavMesh::DecorateMesh off OnServerActivate, and a generated "
		... "mesh takes longer still, so do not cut this too fine.");

	RegServerCmd("sm_survey", Cmd_Survey, "Dump the current map's survey JSON");

	// Deliberately no AutoExecConfig. SourceMod executes a plugin's autoconfig
	// *after* server.cfg, so cfg/sourcemod/mapmaker_survey.cfg would silently
	// reset every mm_survey_* cvar that tools/survey.sh had just set - the
	// server sits there having been told to survey and then told not to, and
	// nothing in the log says so. The generated server cfg is the only
	// configuration this plugin has.

	// tools/survey.sh loads this plugin from the server cfg, so on the first
	// map the plugin does not exist yet when OnMapStart fires and would never
	// arm. Arm here too when a map is already running.
	char running[PLATFORM_MAX_PATH];
	if (GetCurrentMap(running, sizeof(running)) > 0)
		ArmSurvey("plugin start");
}

public void OnMapStart()
{
	g_surveyed = false;
	ArmSurvey("map start");
}

/**
 * Fired after server.cfg and every autoexec config. This is the hook that
 * matters: mm_survey_auto is set *by* the server cfg, so at OnMapStart it is
 * still the default 0 and reading it there decides nothing.
 */
public void OnConfigsExecuted()
{
	ArmSurvey("configs executed");
}

/** Re-armable, and idempotent per map: the last caller wins, once. */
static void ArmSurvey(const char[] why)
{
	if (g_surveyed || g_cvDelay == null)
		return;
	g_armToken++;
	CreateTimer(g_cvDelay.FloatValue, Timer_AutoSurvey, g_armToken);
	LogMessage("[survey] armed at %s: auto=%d delay=%.1f",
		why, g_cvAuto.BoolValue ? 1 : 0, g_cvDelay.FloatValue);
}

public Action Timer_AutoSurvey(Handle timer, any token)
{
	// A survey that fires twice does not just write the file twice: it calls
	// AdvanceMap twice, and over a rotation that silently skips every other
	// map.
	if (token != g_armToken || g_surveyed)
		return Plugin_Stop;

	// Read the cvar now rather than when the timer was armed: the server cfg
	// may not have run yet at arming time.
	if (!g_cvAuto.BoolValue)
		return Plugin_Stop;

	g_surveyed = true;

	char path[PLATFORM_MAX_PATH];
	if (DoSurvey(path, sizeof(path)))
		LogMessage("[survey] wrote %s", path);
	else
		// toujane_b2 read 0 areas and the rotation moved on without a word,
		// so the map looked surveyed until someone counted the files.
		LogError("[survey] FAILED on this map - no file written, moving on");
	AdvanceMap();
	return Plugin_Stop;
}

public Action Cmd_Survey(int args)
{
	char path[PLATFORM_MAX_PATH];
	if (DoSurvey(path, sizeof(path)))
		PrintToServer("[survey] wrote %s", path);
	else
		PrintToServer("[survey] failed - see the log");
	return Plugin_Handled;
}

/**
 * The rotation, as a list of map names.
 *
 * Read from mm_survey_list_file when that names a file that opens, and from
 * mm_survey_list otherwise. The file is the one that scales: a cvar is set by
 * a console command, and the engine tokenises a command into a 512-byte
 * buffer, so `mm_survey_list "<34 workshop maps>"` - 597 bytes - never
 * arrives. The cvar reads back empty and the rotation stops dead after the
 * first map, silently, which is exactly how this was found.
 */
static ArrayList ReadRotation()
{
	ArrayList maps = new ArrayList(PLATFORM_MAX_PATH);

	char file[PLATFORM_MAX_PATH];
	g_cvListFile.GetString(file, sizeof(file));
	if (file[0] != '\0')
	{
		char path[PLATFORM_MAX_PATH];
		BuildPath(Path_SM, path, sizeof(path), "../../%s", file);

		File f = OpenFile(path, "r");
		if (f == null)
		{
			LogError("[survey] mm_survey_list_file names %s, which will not open", path);
		}
		else
		{
			char line[PLATFORM_MAX_PATH];
			while (f.ReadLine(line, sizeof(line)))
			{
				TrimString(line);
				// The file is generated, but it is also editable by hand.
				if (line[0] == '\0' || (line[0] == '/' && line[1] == '/'))
					continue;
				maps.PushString(line);
			}
			delete f;
			return maps;
		}
	}

	char list[1024];
	g_cvList.GetString(list, sizeof(list));
	if (list[0] == '\0')
		return maps;

	char parts[64][PLATFORM_MAX_PATH];
	int n = ExplodeString(list, " ", parts, sizeof(parts), sizeof(parts[]));
	for (int i = 0; i < n; i++)
	{
		TrimString(parts[i]);
		if (parts[i][0] != '\0')
			maps.PushString(parts[i]);
	}
	return maps;
}

/** Round-robin through the rotation so a whole collection surveys unattended. */
static void AdvanceMap()
{
	ArrayList maps = ReadRotation();
	int n = maps.Length;
	if (n == 0)
	{
		// Never return from here without saying so. A survey batch that stops
		// after one map and logs nothing is indistinguishable from a hang.
		LogMessage("[survey] no rotation configured - staying on this map");
		delete maps;
		return;
	}

	char current[PLATFORM_MAX_PATH];
	GetCurrentMap(current, sizeof(current));

	int at = -1;
	char name[PLATFORM_MAX_PATH];
	for (int i = 0; i < n; i++)
	{
		maps.GetString(i, name, sizeof(name));
		if (StrEqual(name, current, false))
		{
			at = i;
			break;
		}
	}
	if (at < 0)
		LogMessage("[survey] %s is not in the rotation - resuming from the top", current);

	int next = at + 1;
	if (next >= n)
	{
		LogMessage("[survey] rotation complete (%d maps)", n);
		delete maps;
		// Without this the server sits at the last map forever and the batch
		// has to be killed from outside - which means something outside has to
		// decide it is finished, from the log. That watcher is easy to get
		// wrong (matching a *previous* run's completion line kills a healthy
		// server), so the process that knows it is done ends itself.
		if (g_cvQuit.BoolValue)
		{
			LogMessage("[survey] mm_survey_quit is set - shutting down");
			ServerCommand("quit");
		}
		return;
	}

	maps.GetString(next, name, sizeof(name));
	LogMessage("[survey] next: %s (%d of %d)", name, next + 1, n);
	ServerCommand("changelevel %s", name);
	delete maps;
}

// ===========================================================================
// Area enumeration
// ===========================================================================

static void IndexArea(Address area)
{
	char key[16];
	Format(key, sizeof(key), "%x", view_as<int>(area));
	int existing;
	if (g_areaIndex.GetValue(key, existing))
		return;
	if (g_areaCount >= MM_MAX_AREAS)
	{
		if (!g_areaOverflow)
		{
			g_areaOverflow = true;
			LogError("[survey] area cap %d reached - THIS SURVEY IS TRUNCATED "
				... "and its graph is cut. Raise MM_MAX_AREAS and re-run.",
				MM_MAX_AREAS);
		}
		return;
	}
	g_areaIndex.SetValue(key, g_areaCount);
	g_areas[g_areaCount++] = area;
}

static bool AreaIndexOf(Address area, int &out)
{
	char key[16];
	Format(key, sizeof(key), "%x", view_as<int>(area));
	return g_areaIndex.GetValue(key, out);
}

/**
 * Whole-mesh enumeration when TheNavAreas resolved, otherwise a flood from
 * every spawn entity on the map.
 *
 * The fallback is not a consolation prize: the reachable component is the part
 * the layout metrics score anyway. It just cannot report what it never saw, so
 * the JSON records which method produced it.
 */
static bool CollectAreas(char[] method, int methodLen)
{
	g_areaCount = 0;
	g_areaOverflow = false;
	delete g_areaIndex;
	g_areaIndex = new StringMap();

	if (MM_HaveAreaVector())
	{
		int n = MM_GetAreaCount();
		if (n > 0)
		{
			for (int i = 0; i < n && g_areaCount < MM_MAX_AREAS; i++)
			{
				Address a = MM_GetArea(i);
				if (a != Address_Null)
					IndexArea(a);
			}
			if (g_areaCount > 0)
			{
				strcopy(method, methodLen, "TheNavAreas");
				return true;
			}
		}
		LogMessage("[survey] TheNavAreas resolved but read %d areas - falling back to flood", n);
	}

	// Seed from every spawn point and objective on the map.
	int seeds = 0;
	for (int c = 0; c < sizeof(g_classes); c++)
	{
		int ent = -1;
		while ((ent = FindEntityByClassname(ent, g_classes[c])) != -1)
		{
			float origin[3];
			if (!GetEntityOrigin(ent, origin))
				continue;
			Address a = MM_GetNearestNavArea(origin, true, 1024.0);
			if (a == Address_Null)
				continue;
			int before = g_areaCount;
			IndexArea(a);
			if (g_areaCount > before)
				seeds++;
		}
	}
	if (seeds == 0)
	{
		LogError("[survey] no seed areas - cannot enumerate the mesh");
		return false;
	}

	// BFS. g_areas doubles as the queue: everything appended is unvisited.
	for (int head = 0; head < g_areaCount; head++)
	{
		Address cur = g_areas[head];
		for (int dir = 0; dir < 4; dir++)
		{
			int adj = MM_NavArea_GetAdjacentCount(cur, dir);
			for (int i = 0; i < adj; i++)
			{
				Address nb = MM_NavArea_GetAdjacentArea(cur, dir, i);
				if (nb != Address_Null)
					IndexArea(nb);
			}
		}
	}

	Format(method, methodLen, "flood from %d seeds", seeds);
	return g_areaCount > 0;
}

// ===========================================================================
// Helpers
// ===========================================================================

static bool GetEntityOrigin(int ent, float out[3])
{
	if (!IsValidEntity(ent))
		return false;
	if (HasEntProp(ent, Prop_Data, "m_vecAbsOrigin"))
	{
		GetEntPropVector(ent, Prop_Data, "m_vecAbsOrigin", out);
		return true;
	}
	if (HasEntProp(ent, Prop_Send, "m_vecOrigin"))
	{
		GetEntPropVector(ent, Prop_Send, "m_vecOrigin", out);
		return true;
	}
	return false;
}

static bool GetEntityBounds(int ent, float mins[3], float maxs[3])
{
	if (HasEntProp(ent, Prop_Send, "m_vecMins") && HasEntProp(ent, Prop_Send, "m_vecMaxs"))
	{
		GetEntPropVector(ent, Prop_Send, "m_vecMins", mins);
		GetEntPropVector(ent, Prop_Send, "m_vecMaxs", maxs);
		return true;
	}
	if (HasEntProp(ent, Prop_Data, "m_vecMins") && HasEntProp(ent, Prop_Data, "m_vecMaxs"))
	{
		GetEntPropVector(ent, Prop_Data, "m_vecMins", mins);
		GetEntPropVector(ent, Prop_Data, "m_vecMaxs", maxs);
		return true;
	}
	return false;
}

static void ParseVector(ConVar cv, float out[3])
{
	char buf[64], parts[3][16];
	cv.GetString(buf, sizeof(buf));
	if (ExplodeString(buf, " ", parts, sizeof(parts), sizeof(parts[])) != 3)
	{
		out[0] = out[1] = out[2] = 0.0;
		return;
	}
	for (int i = 0; i < 3; i++)
		out[i] = StringToFloat(parts[i]);
}

/**
 * The same shape as CINSRules::IsSpawnPointValid: a player-hull fit against
 * the real collision world. This is the oracle the offline predictors could
 * not reproduce, so it is exported as a column rather than re-derived later.
 */
static bool HullFits(float pos[3], float mins[3], float maxs[3])
{
	TR_TraceHull(pos, pos, mins, maxs, MASK_PLAYERSOLID);
	return !TR_DidHit() && !TR_StartSolid();
}

static void JsonEscape(const char[] src, char[] out, int len)
{
	strcopy(out, len, src);
	ReplaceString(out, len, "\\", "\\\\");
	ReplaceString(out, len, "\"", "\\\"");
}

// ===========================================================================
// The dump
// ===========================================================================

static bool DoSurvey(char[] outPath, int outPathLen)
{
	if (!g_navReady)
		return false;

	if (MM_GetTheNavMesh() == Address_Null)
	{
		LogError("[survey] TheNavMesh is null - no mesh loaded yet?");
		return false;
	}

	char method[64];
	if (!CollectAreas(method, sizeof(method)))
		return false;

	// Probe the corner offsets against real areas before trusting them.
	Address samples[16];
	int nsamples = g_areaCount < 16 ? g_areaCount : 16;
	int stride = g_areaCount / (nsamples > 0 ? nsamples : 1);
	if (stride < 1)
		stride = 1;
	for (int i = 0; i < nsamples; i++)
		samples[i] = g_areas[i * stride];

	bool haveCorners = MM_NavProbeCorners(samples, nsamples);
	int nwOff, seOff;
	MM_NavCornerOffsets(nwOff, seOff);
	if (haveCorners)
		LogMessage("[survey] corner offsets nw=%d se=%d (put these in gamedata)", nwOff, seOff);
	else
		LogMessage("[survey] corner offsets not found - footprints omitted, centres still exported");

	char map[PLATFORM_MAX_PATH];
	GetCurrentMap(map, sizeof(map));

	char dir[PLATFORM_MAX_PATH], out[PLATFORM_MAX_PATH];
	g_cvOut.GetString(dir, sizeof(dir));
	BuildPath(Path_SM, out, sizeof(out), "../../%s", dir);
	if (!DirExists(out))
		CreateDirectory(out, 511);
	Format(outPath, outPathLen, "%s/%s.json", out, map);

	File f = OpenFile(outPath, "w");
	if (f == null)
	{
		LogError("[survey] cannot write %s", outPath);
		return false;
	}

	float hullMin[3], hullMax[3];
	ParseVector(g_cvHullMin, hullMin);
	ParseVector(g_cvHullMax, hullMax);

	f.WriteLine("{");
	f.WriteLine("  \"map\": \"%s\",", map);
	f.WriteLine("  \"schema\": 1,");
	f.WriteLine("  \"enumeration\": \"%s\",", method);
	f.WriteLine("  \"area_count\": %d,", g_areaCount);
	f.WriteLine("  \"truncated\": %s,", g_areaOverflow ? "true" : "false");
	f.WriteLine("  \"corner_offsets\": %s,", haveCorners ? "true" : "false");
	f.WriteLine("  \"hull\": {\"mins\": [%.1f, %.1f, %.1f], \"maxs\": [%.1f, %.1f, %.1f]},",
		hullMin[0], hullMin[1], hullMin[2], hullMax[0], hullMax[1], hullMax[2]);

	WriteAreas(f, haveCorners, hullMin, hullMax);
	WriteEntities(f);
	WriteVisibility(f);

	f.WriteLine("  \"end\": true");
	f.WriteLine("}");
	delete f;
	return true;
}

static void WriteAreas(File f, bool haveCorners, float hullMin[3], float hullMax[3])
{
	f.WriteLine("  \"areas\": [");
	for (int i = 0; i < g_areaCount; i++)
	{
		Address a = g_areas[i];

		float c[3];
		MM_NavArea_GetCenter(a, c);

		float nw[3], se[3];
		if (haveCorners)
			MM_NavArea_GetCorners(a, nw, se);

		// Trace from the area centre and from four interior offsets, so a
		// centre that happens to sit under a prop does not condemn the area.
		bool hullOk = HullFits(c, hullMin, hullMax);
		int hullHits = hullOk ? 1 : 0;
		if (haveCorners)
		{
			float qx = (se[0] - nw[0]) * 0.25;
			float qy = (se[1] - nw[1]) * 0.25;
			float dx[4], dy[4];
			dx[0] = -qx; dy[0] = 0.0;
			dx[1] =  qx; dy[1] = 0.0;
			dx[2] = 0.0; dy[2] = -qy;
			dx[3] = 0.0; dy[3] =  qy;
			for (int p = 0; p < 4; p++)
			{
				float probe[3];
				probe[0] = c[0] + dx[p];
				probe[1] = c[1] + dy[p];
				probe[2] = c[2];
				if (HullFits(probe, hullMin, hullMax))
					hullHits++;
			}
		}

		f.WriteString("    {", false);
		f.WriteString("\"i\": ", false);
		WriteFmt(f, "%d, \"center\": [%.2f, %.2f, %.2f]", i, c[0], c[1], c[2]);
		if (haveCorners)
			WriteFmt(f, ", \"nw\": [%.2f, %.2f, %.2f], \"se\": [%.2f, %.2f, %.2f]",
				nw[0], nw[1], nw[2], se[0], se[1], se[2]);
		WriteFmt(f, ", \"flags\": %d, \"indoor\": %s",
			MM_NavArea_GetInsFlags(a), MM_NavArea_IsIndoor(a) ? "true" : "false");
		WriteFmt(f, ", \"blocked\": %s", MM_NavArea_IsBlocked(a) ? "true" : "false");
		WriteFmt(f, ", \"hull_ok\": %s, \"hull_probes\": %d",
			hullOk ? "true" : "false", hullHits);

		// Connections, as index pairs with the engine's own edge lengths.
		f.WriteString(", \"conn\": [", false);
		bool firstConn = true;
		for (int dir = 0; dir < 4; dir++)
		{
			int adj = MM_NavArea_GetAdjacentCount(a, dir);
			for (int k = 0; k < adj; k++)
			{
				Address nb = MM_NavArea_GetAdjacentArea(a, dir, k);
				int ni;
				if (nb == Address_Null || !AreaIndexOf(nb, ni))
					continue;
				WriteFmt(f, "%s[%d, %d, %.1f]", firstConn ? "" : ", ",
					dir, ni, MM_NavArea_GetAdjacentLength(a, dir, k));
				firstConn = false;
			}
		}
		f.WriteString("]}", false);
		f.WriteLine("%s", (i == g_areaCount - 1) ? "" : ",");
	}
	f.WriteLine("  ],");
}

static void WriteEntities(File f)
{
	f.WriteLine("  \"entities\": [");
	bool first = true;
	for (int c = 0; c < sizeof(g_classes); c++)
	{
		int ent = -1;
		while ((ent = FindEntityByClassname(ent, g_classes[c])) != -1)
		{
			float origin[3];
			if (!GetEntityOrigin(ent, origin))
				continue;

			char name[128], nameEsc[256], model[128], modelEsc[256];
			name[0] = '\0';
			model[0] = '\0';
			if (HasEntProp(ent, Prop_Data, "m_iName"))
				GetEntPropString(ent, Prop_Data, "m_iName", name, sizeof(name));
			if (HasEntProp(ent, Prop_Data, "m_ModelName"))
				GetEntPropString(ent, Prop_Data, "m_ModelName", model, sizeof(model));
			JsonEscape(name, nameEsc, sizeof(nameEsc));
			JsonEscape(model, modelEsc, sizeof(modelEsc));

			int team = 0;
			if (HasEntProp(ent, Prop_Send, "m_iTeamNum"))
				team = GetEntProp(ent, Prop_Send, "m_iTeamNum");
			else if (HasEntProp(ent, Prop_Data, "m_iTeamNum"))
				team = GetEntProp(ent, Prop_Data, "m_iTeamNum");

			if (!first)
				f.WriteLine(",");
			first = false;

			f.WriteString("    {", false);
			WriteFmt(f, "\"class\": \"%s\", \"target\": \"%s\", \"team\": %d",
				g_classes[c], nameEsc, team);
			WriteFmt(f, ", \"origin\": [%.2f, %.2f, %.2f]", origin[0], origin[1], origin[2]);
			if (model[0] != '\0')
				WriteFmt(f, ", \"model\": \"%s\"", modelEsc);

			// Model bounds. Brush volumes translate but do not resize, so the
			// only way to widen a zone is to borrow another brush's model -
			// which needs this table to be a choice rather than a guess.
			float mins[3], maxs[3];
			if (GetEntityBounds(ent, mins, maxs))
				WriteFmt(f, ", \"mins\": [%.2f, %.2f, %.2f], \"maxs\": [%.2f, %.2f, %.2f]",
					mins[0], mins[1], mins[2], maxs[0], maxs[1], maxs[2]);

			// Which area the entity stands in, so the generator can join the
			// entity table to the graph without re-snapping offline.
			Address a = MM_GetNearestNavArea(origin, true, 512.0);
			int ai;
			if (a != Address_Null && AreaIndexOf(a, ai))
				WriteFmt(f, ", \"area\": %d", ai);

			f.WriteString("}", false);
		}
	}
	if (!first)
		f.WriteLine("");
	f.WriteLine("  ],");
}

static void WriteVisibility(File f)
{
	if (!g_cvVis.BoolValue)
	{
		f.WriteLine("  \"visibility\": null,");
		return;
	}

	LogMessage("[survey] visibility: %d areas, %d pairs - this will stall the server",
		g_areaCount, g_areaCount * g_areaCount);

	f.WriteLine("  \"visibility\": [");
	for (int i = 0; i < g_areaCount; i++)
	{
		f.WriteString("    [", false);
		bool first = true;
		for (int j = 0; j < g_areaCount; j++)
		{
			if (i == j)
				continue;
			if (!MM_NavArea_IsPotentiallyVisible(g_areas[i], g_areas[j]))
				continue;
			WriteFmt(f, "%s%d", first ? "" : ",", j);
			first = false;
		}
		f.WriteString("]", false);
		f.WriteLine("%s", (i == g_areaCount - 1) ? "" : ",");
	}
	f.WriteLine("  ],");
}

/** File.WriteString has no format overload; this keeps the call sites short. */
static void WriteFmt(File f, const char[] fmt, any ...)
{
	char buf[512];
	VFormat(buf, sizeof(buf), fmt, 3);
	f.WriteString(buf, false);
}
