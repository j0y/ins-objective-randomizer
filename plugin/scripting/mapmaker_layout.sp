/**
 * mapmaker_layout.sp — stage C of docs/layout-variants.md: apply a generated
 * layout preset at map load, and rotate presets between rounds.
 *
 * Deliberately dumb. Every decision was made offline by the generator; this
 * plugin looks up a preset, rewrites origins in the entity lump before any
 * entity is created, and reports what it did. It does no scoring of its own,
 * because anything it got wrong would be invisible until someone played it.
 *
 * NOTE, against §7 of docs/layout-variants.md: that document cites
 *     forward Action OnLevelInit(const char[] mapName, char mapEntities[2097152]);
 * from sdkhooks.inc as the mechanism. On SourceMod 1.11 that forward is
 * deprecated and its buffer is documented "Unused, always empty" - it would
 * silently do nothing. The mechanism still exists and is now a first-class
 * API: the EntityLump natives, writable during OnMapInit. That is strictly
 * better than string surgery on a 2 MB buffer, because the lump arrives
 * already parsed into key/value entries.
 *
 * The effect is the one the document describes: the map loads as though it
 * had shipped that way, nothing downstream can observe the difference, and
 * clients join stock - there is no patched .bsp and nothing to download.
 *
 * Caches live in maps/<name>.txt rather than the lump, so they are moved after
 * they spawn instead. CObjWeaponCache::Teleport is overridden in the server
 * binary and fixes up the cache's own trigger, so the game already supports
 * this. One .txt then serves every variant.
 *
 *   sm_preset             which layout is this, in one line - anyone may ask
 *   sm_layout             the same, with the pin and the toggle
 *   sm_layout list        list presets for this map
 *   sm_layout <name>      pin one for the next load
 *   sm_layout stock       load the map unmodified
 *   sm_layout off | on    stop / resume applying presets, from the next load
 */

#pragma semicolon 1
#pragma newdecls required

#include <sourcemod>
#include <sdktools>
#include <entitylump>

#define MM_MAX_PRESETS 32

// A reversal moves every spawn point on the map, individually: the coordinate
// each one gets is chosen offline against the survey's hull probe, so there is
// nothing to be gained from a group translation and a lot to lose. That is 444
// rules on ministry_coop and 761 on market_coop.
#define MM_MAX_EDITS 1024

public Plugin myinfo =
{
	name        = "mapmaker layout",
	author      = "j0y",
	description = "Rotate generated objective/spawn layouts at map load",
	version     = "0.1.0",
	url         = ""
};

static ConVar g_cvEnabled;
static ConVar g_cvDir;
static ConVar g_cvMaxFailures;
static ConVar g_cvHullMin;
static ConVar g_cvHullMax;
static ConVar g_cvDebug;
static ConVar g_cvBlockZones;
static ConVar g_cvAnnounce;
// The engine's own mp_gamemode, looked up once. null on a game that has no
// such cvar, which is the one case the gate cannot be enforced in.
static ConVar g_cvGameMode;

static char g_pinned[64];          // sm_layout <name>, "" for rotate, "stock" for none
static char g_activeName[64];      // preset applied to the running map
// True until an OnMapInit says otherwise, because the first load of a session
// never gets one: the server config is what loads this plugin and the engine
// runs it after that map's OnMapInit. Nothing was applied to that map, so
// nothing is re-applied to it either - it is stock, and the post-load pass
// measures it as the control every later load is read against.
static bool g_activeIsStock = true;
// Why this map is stock, when it is. Four different things produce an
// unmodified map - the toggle is off, stock is pinned, the map has no preset
// file, or the preset it picked asked for nothing - and "stock" alone cannot
// be acted on: the first is a switch someone flipped, the third is a map the
// generator has never emitted for. See PresetLine.
// Its initial value is the boot map's answer and is never assigned there,
// which is the point: the server config is what loads this plugin and the
// engine runs it *after* the first map's OnMapInit, so on that one map this
// plugin's OnMapInit never happened and nothing was applied. Saying "no preset
// was read" would be true and useless.
static char g_activeWhy[160] =
	"this is the server's boot map - the config that loads the applier runs "
	... "after its map init, so nothing was applied to it";
// Which of the file's presets was picked, and how many there were. A player
// reporting a bad round names the preset; whoever reads that report wants to
// know it was 3 of 16 rather than the only one there is.
static int  g_activeIndex = -1;
static int  g_presetCount;
// Rules that matched no lump entry. Counted at load anyway for the log line;
// kept because it is the one number that says "this preset is stale against
// this map", which is a different complaint from "this layout plays badly".
static int  g_activeUnmatched;
// Whether this map's line has been put in chat yet. Round start is when
// everyone is alive to read it, and only the first round of a map: the preset
// cannot change under them.
static bool g_announced;
// What the console last said about the toggle: -1 nothing, 0 off, 1 on.
//
// A server config is executed on *every* map load, so a config line setting
// `mm_layout_enabled` puts it back after each one - measured 2026-09-19 on a
// `layout-server.sh --stock` server, where `sm_layout on` bought exactly one
// layouted load before the next config exec turned it off again. The pin
// (`g_pinned`) has always outranked the file for *which* preset; this is the
// same thing for *whether*, and it is why there is still only one switch -
// the override does not shadow the cvar, it re-asserts it.
static int  g_enabledOverride = -1;
// Set while the plugin is doing that re-assertion, so the change hook logs it
// as housekeeping rather than announcing a change nobody made.
static bool g_reasserting;
// True between this map's OnMapInit and the end of its config execution.
//
// It is what tells a person from a file. The two ways `mm_layout_enabled`
// changes are someone typing it and the per-load server config executing it,
// and they want opposite treatment: a person has just overridden the config
// and should be heard, a config has just overridden the person and should be
// put back. Nothing in the change hook says which - but the config's turn is
// exactly this window, and a person's almost never is. So a change inside it
// is the file's and is answered by OnConfigsExecuted; a change outside it is
// an instruction, and `mm_layout_enabled 0` at the console then means what
// `sm_layout off` means, which is the point of there being one switch.
static bool g_loading;
static int  g_editsApplied;
static int  g_editsSkipped;
static int  g_blockZonesOff;      // restricted areas started disabled this load
static int  g_blockZonesUnbound;  // spawn zones that no longer name one

// Objective moves deferred to OnMapStart, since caches come from the .txt.
static char  g_objName[32][64];
static char  g_objCp[32][64];
static float g_objOrigin[32][3];
static float g_objCpOffset[32];
// Where the two entities stand on the stock map. A targetname is not unique -
// nine of the 46 surveyed maps carry two entities under one objective name,
// because maps/<map>.txt creates its own marker beside the one the mapper
// baked into the BSP, and on congress_coop those two sit 1,696 u apart. The
// name alone therefore picks whichever the enumeration reaches first; these
// say which one the preset was measured from.
static float g_objFrom[32][3];
static float g_objCpFrom[32][3];
static bool  g_objHasFrom[32];
static bool  g_objHasCpFrom[32];
static int   g_objCount;

// The edit rules, read out of the preset before the lump is walked. They are
// held rather than applied one at a time because applying one at a time means
// one pass over the lump per rule: 761 rules against ~1,900 entries is 1.4M
// EntityLumpEntry handles, in OnMapInit, to make 761 edits.
static char  g_eClass[MM_MAX_EDITS][32];
static char  g_eTarget[MM_MAX_EDITS][64];
static int   g_eTeam[MM_MAX_EDITS];
static bool  g_eHasBox[MM_MAX_EDITS];
static float g_eBox[MM_MAX_EDITS][6];
static char  g_eOrigin[MM_MAX_EDITS][64];
static bool  g_eHasOffset[MM_MAX_EDITS];
static float g_eOffset[MM_MAX_EDITS][3];
static char  g_eRename[MM_MAX_EDITS][64];
static int   g_eCount;

// What each rule did to the lump, kept so the running map can be read back
// against the rule rather than against an aggregate. A rule that matched no
// entry, an origin the engine ignored, a brush that did not follow its origin
// and a rename that did not stick are four bugs with four fixes, and as one
// hull count they are one number.
static int   g_eMatches[MM_MAX_EDITS];      // entries this rule edited
static int   g_eShadowed[MM_MAX_EDITS];     // entries an earlier rule had taken
static float g_eWas[MM_MAX_EDITS][3];       // origin found, last match
static float g_eIntended[MM_MAX_EDITS][3];  // origin written, last match
static int   g_eOrdinal[MM_MAX_EDITS];      // which entry of its class, in lump order
static int   g_eVerdict[MM_MAX_EDITS];
static int   g_eEntity[MM_MAX_EDITS];       // entity the verdict was read off
static float g_eOffBy[MM_MAX_EDITS];        // how far that entity ended up from the rule
static bool  g_eNoOrigin[MM_MAX_EDITS];     // the entry it took carried no origin key
static int   g_objFailed;
// Objective passes per round start, one every half second. See Timer_RoundObjectives.
#define MM_ROUND_PASSES 12
// Slots in CINSObjectiveResource's networked position array. See UpdateCPMarker.
#define MM_MAX_CONTROL_POINTS 16
static int   g_objPass;

// How many entries of each classname the lump held, which is what makes the
// ordinal above worth anything: if the map spawned a different number than the
// lump described, the k-th entity is not the k-th entry and every verdict for
// that class is suspect. Cheaper to record it and say so than to be wrong.
static StringMap g_lumpClassCount;

// Rule outcomes, worst first: a rule is given the first one that applies.
enum
{
	MMV_UNMATCHED = 0,  // nothing in the lump matched - the preset is stale
	MMV_SHADOWED,       // an earlier rule had already edited the only match
	MMV_MISSING,        // edited, but the map spawned no entity for that entry
	MMV_STUCK,          // the entity is still on the origin the rule replaced
	MMV_NUDGED,         // it took the move and the engine then adjusted it
	MMV_ADRIFT,         // the entity moved and its brush volume did not
	MMV_MISNAMED,       // the rename did not stick
	MMV_HULL,           // placed, but a player does not fit there
	MMV_OK,
	MMV_COUNT
};

static const char g_verdict[][] = {
	"unmatched", "shadowed", "missing", "stuck", "nudged", "adrift", "misnamed",
	"hull", "ok"
};

// Origins are written to 2 dp, so an entity that took its edit reads back on
// top of it; anything further away is a different entity.
#define MM_VERIFY_TOL          2.0
// Far enough out to be a different place, close enough to be the engine having
// adjusted the one we asked for - a ground snap, a nudge out of a wall.
#define MM_VERIFY_NEAR         64.0
#define MM_MAX_VERIFY_ENTS     1024
#define MM_MAX_VERIFY_CLASSES  16
// Failing rules logged in full before the rest become a count. A reversal is
// 444 rules and a preset that is wholly stale would otherwise bury the log.
#define MM_VERIFY_DETAIL       20

static int   g_vEnt[MM_MAX_VERIFY_ENTS];
static float g_vPos[MM_MAX_VERIFY_ENTS][3];

// ===========================================================================
// Lifecycle
// ===========================================================================

public void OnPluginStart()
{
	g_cvEnabled = CreateConVar("mm_layout_enabled", "1",
		"Apply layout presets at map load");
	g_cvDir = CreateConVar("mm_layout_dir", "presets",
		"Directory under the game dir holding <map>.cfg preset files");
	g_cvMaxFailures = CreateConVar("mm_layout_max_failures", "3",
		"If more than this many placed points fail the post-load hull test, "
		... "log loudly. A rule that did not take at all is loud regardless - "
		... "that is a stale preset rather than an awkward coordinate. Presets "
		... "go stale when a workshop author updates a map; this is what makes "
		... "stale mean 'noisy', not 'silently broken'.");
	g_cvHullMin = CreateConVar("mm_layout_hull_min", "-16 -16 0", "Player hull mins");
	g_cvHullMax = CreateConVar("mm_layout_hull_max", "16 16 72", "Player hull maxs");
	g_cvDebug = CreateConVar("mm_layout_debug", "0", "Log every lump edit");
	g_cvBlockZones = CreateConVar("mm_layout_blockzones", "0",
		"0 starts every ins_blockzone disabled and takes the blockzone key off "
		... "every ins_spawnzone; 1 leaves restricted areas as the map "
		... "authored them. A restricted area holds the attackers behind the "
		... "objective, and permuting the ground under the chain moves which "
		... "side that is, so a layout can be walled off its own route by a "
		... "volume the preset never touched - which is why removal is the "
		... "default and keeping them is the exception. Only a map that is "
		... "applying a preset is affected: a stock load edits no lump entry "
		... "at all.");

	g_cvAnnounce = CreateConVar("mm_layout_announce", "0",
		"Say which layout is running in chat, once per map and once to each "
		... "player who joins. Off by default: a player told on load which of "
		... "N walks this is knows the map is permuted and how many "
		... "permutations there are before the round begins, which is the "
		... "thing a permuted map exists to avoid. The name is still "
		... "reachable - `sm_preset` answers anyone who asks for it, and the "
		... "log carries it either way - so the report a bad round needs is "
		... "one command away rather than pushed at everybody. 1 when a "
		... "playtest wants it unprompted.");

	// Reachable by anyone, because the person who needs the name is the person
	// who has just played a bad round rather than whoever holds the admin
	// flag. It reports and changes nothing, so there is nothing to gate.
	RegConsoleCmd("sm_preset", Cmd_Preset,
		"Which layout preset this map loaded, and why, if it is stock");

	RegAdminCmd("sm_layout", Cmd_Layout, ADMFLAG_CHANGEMAP,
		"sm_layout [list|stock|off|on|<preset name>]");

	// The toggle takes effect at the next OnMapInit and nowhere else - the
	// lump is rewritten before any entity exists, so there is no undoing it on
	// a running map. Said out loud here rather than left for someone to
	// discover by flipping it and watching nothing happen.
	g_cvEnabled.AddChangeHook(OnEnabledChanged);

	// Looked up once. It is the game's cvar, not ours, and it exists by the
	// time any plugin starts; if it ever does not, the gate below fails open
	// and says so rather than declining to apply anything ever again.
	g_cvGameMode = FindConVar("mp_gamemode");
	if (g_cvGameMode == null)
		LogError("[layout] no mp_gamemode cvar on this game - the checkpoint "
			... "gate cannot be enforced and every mode will be treated as one "
			... "a preset suits");

	// Caches are teleported, not rewritten, so they have to be re-teleported
	// every time the game puts them back. See Event_RoundStart.
	HookEvent("round_start", Event_RoundStart, EventHookMode_PostNoCopy);
	// A capture is the other moment the gamemode reaches for these: the next
	// stage's zone goes up as the current one falls.
	HookEvent("controlpoint_captured", Event_RoundStart);

	RegServerCmd("mm_layout_objdump", Cmd_ObjDump,
		"Log every objective entity in the running map and where it is now");

	// No AutoExecConfig, for the reason in mapmaker_survey.sp: SourceMod runs
	// it after server.cfg and it would undo what tools/layout-server.sh set.
}

// ===========================================================================
// The apply
// ===========================================================================

public void OnMapInit(const char[] mapName)
{
	g_loading = true;
	g_activeName[0] = '\0';
	g_activeIsStock = true;
	strcopy(g_activeWhy, sizeof(g_activeWhy), "no preset was read");
	g_activeIndex = -1;
	g_presetCount = 0;
	g_activeUnmatched = 0;
	g_announced = false;
	g_editsApplied = 0;
	g_editsSkipped = 0;
	g_objCount = 0;
	g_objFailed = 0;
	g_eCount = 0;
	delete g_lumpClassCount;

	if (!g_cvEnabled.BoolValue)
	{
		strcopy(g_activeWhy, sizeof(g_activeWhy), "layouts are off (mm_layout_enabled 0)");
		LogMessage("[layout] %s: stock - %s", mapName, g_activeWhy);
		return;
	}

	// **A preset permutes a checkpoint objective chain, and a `_coop` .bsp is
	// not only ever played as checkpoint.** The same map sits in a rotation
	// under conquer as well, and hunt, survival and outpost run on these maps
	// too - nothing in the name says which game is about to start. Without
	// this the conquer round gets the checkpoint layout: spawn zones permuted
	// around a ladder that mode never climbs, restricted areas taken off,
	// caches teleported to coordinates read out of the .txt's `checkpoint`
	// block. Every gate in the generator measured checkpoint, so checkpoint is
	// the only mode a preset is allowed in.
	//
	// Read here because map init is the only moment the lump can be rewritten,
	// and mp_gamemode is the incoming map's by then: whatever picks the next
	// map sets it before the changelevel. Measured 2026-09-19 over three
	// consecutive loads - caves_coop as checkpoint applied its preset,
	// crossbow as conquer was declined here, crossbow as checkpoint applied.
	//
	// Returning leaves the map alone for good, not just in the lump: no rule
	// is ever read, so the objective burst and the restricted-area pass have
	// nothing to do and Event_RoundStart skips them on g_activeIsStock, which
	// is still true here. The post-load pass still runs, as it does on every
	// stock load, and measures a map nothing touched.
	if (g_cvGameMode != null)
	{
		char mode[32];
		g_cvGameMode.GetString(mode, sizeof(mode));
		if (mode[0] != '\0' && !StrEqual(mode, "checkpoint", false))
		{
			FormatEx(g_activeWhy, sizeof(g_activeWhy),
				"this map is loading as '%s', and a preset is a permutation of "
				... "a checkpoint objective chain", mode);
			LogMessage("[layout] %s: stock - gamemode is '%s', not checkpoint; "
				... "nothing edited", mapName, mode);
			return;
		}
	}

	if (StrEqual(g_pinned, "stock", false))
	{
		strcopy(g_activeWhy, sizeof(g_activeWhy), "stock is pinned (sm_layout stock)");
		LogMessage("[layout] %s: stock (pinned)", mapName);
		return;
	}

	KeyValues kv = LoadPresets(mapName);
	if (kv == null)
	{
		strcopy(g_activeWhy, sizeof(g_activeWhy), "this map has no preset file");
		LogMessage("[layout] %s: no preset file - stock", mapName);
		return;
	}

	char names[MM_MAX_PRESETS][64];
	int n = ListPresets(kv, names, sizeof(names));
	if (n == 0)
	{
		strcopy(g_activeWhy, sizeof(g_activeWhy), "its preset file holds no presets");
		LogMessage("[layout] %s: preset file has no presets - stock", mapName);
		delete kv;
		return;
	}

	// **The control is not rotated to.** Every generated file opens with a
	// `stock` preset, and it used to come round like any other member - one
	// load in N of every map spent on the map as it ships. That was right when
	// the rotation was the only way to reach the control; it is not now. The
	// boot load of every session is stock because this plugin is not loaded at
	// its map init, `sm_layout off` and `layout-server.sh --stock` turn the
	// applier off outright, and `sm_layout stock` pins it for one map. Three
	// ways in, and a rotation slot is the expensive one: on bombshelter, which
	// carries two presets, it was every other load.
	//
	// The entry stays in the file - it carries the stock round's own metrics,
	// which is what every gate in the generator measures a layout against, and
	// `sm_layout list` still shows it.
	int rot = 0;
	for (int i = 0; i < n; i++)
	{
		if (StrEqual(names[i], "stock", false))
			continue;
		if (rot != i)
			strcopy(names[rot], sizeof(names[]), names[i]);
		rot++;
	}
	// ...unless that is all there is. A map with nothing but the control is a
	// map the generator emitted no layout for, and stock is then not a slot
	// being spent, it is the only answer there is.
	if (rot == 0)
	{
		strcopy(g_activeWhy, sizeof(g_activeWhy),
			"its preset file holds only the stock control");
		LogMessage("[layout] %s: preset file holds only the control - stock", mapName);
		delete kv;
		return;
	}
	n = rot;
	g_presetCount = n;

	int pick = ChoosePreset(mapName, names, n);
	if (pick < 0)
	{
		delete kv;
		return;
	}

	if (!SelectPreset(kv, names[pick]))
	{
		LogError("[layout] %s: preset '%s' vanished between listing and selection",
			mapName, names[pick]);
		delete kv;
		return;
	}

	LoadEdits(kv);
	ApplyEdits();
	CollectObjectives(kv);
	delete kv;

	strcopy(g_activeName, sizeof(g_activeName), names[pick]);
	g_activeIndex = pick;
	// Stock means the preset asked for nothing, not that nothing happened. A
	// preset whose every rule missed applied no edits either, and treating
	// that as stock would skip the post-load pass - so the one load that most
	// needs a verdict would be the one load that never gets one.
	g_activeIsStock = (g_eCount == 0 && g_objCount == 0);
	if (g_activeIsStock)
		FormatEx(g_activeWhy, sizeof(g_activeWhy),
			"preset '%s' asked for no edits", g_activeName);

	// A rule that matched no entry is known here, five seconds before the
	// verification pass can say anything, and it is the cheapest signal that a
	// preset has gone stale against a map that moved under it.
	int unmatched = 0;
	for (int r = 0; r < g_eCount; r++)
		if (g_eMatches[r] == 0)
			unmatched++;
	g_activeUnmatched = unmatched;

	// Announce at load, so a demo, a complaint or a playtest note is
	// traceable to a layout.
	LogMessage("[layout] %s: preset '%s' - %d edits applied, %d entries skipped, "
		... "%d of %d rules matched nothing, %d objectives deferred",
		mapName, g_activeName, g_editsApplied, g_editsSkipped,
		unmatched, g_eCount, g_objCount);

	if (g_blockZonesOff > 0)
		LogMessage("[layout] %s: %d restricted area(s) start disabled, %d spawn "
			... "zone(s) no longer name one (mm_layout_blockzones 0)",
			mapName, g_blockZonesOff, g_blockZonesUnbound);
}

/**
 * Every objective entity in the map, by class, with its name and where it is
 * standing right now. Reads nothing the plugin remembers, so it answers after
 * a reload and it answers for a stock map: the question it exists for is
 * "which entity is the game actually using", and a preset that names one of
 * two entities with the same targetname looks correct in every other report.
 */
public Action Cmd_ObjDump(int args)
{
	// mm_layout_objdump <x> <y> <z> [radius] - every entity standing near a
	// coordinate, whatever its class. A moved objective that stops working
	// left something behind, and the only way to find out what is to look at
	// the ground it was standing on.
	if (args >= 3)
	{
		char buf[32];
		float centre[3], radius = 256.0;
		for (int a = 0; a < 3; a++)
		{
			GetCmdArg(a + 1, buf, sizeof(buf));
			centre[a] = StringToFloat(buf);
		}
		if (args >= 4)
		{
			GetCmdArg(4, buf, sizeof(buf));
			radius = StringToFloat(buf);
		}
		int found = 0;
		for (int ent = 1; ent < GetMaxEntities(); ent++)
		{
			if (!IsValidEntity(ent))
				continue;
			float pos[3];
			if (!GetEntityOrigin(ent, pos))
				continue;
			if (GetVectorDistance(pos, centre) > radius)
				continue;
			char class[64], name[64];
			class[0] = '\0'; name[0] = '\0';
			GetEntityClassname(ent, class, sizeof(class));
			if (HasEntProp(ent, Prop_Data, "m_iName"))
				GetEntPropString(ent, Prop_Data, "m_iName", name, sizeof(name));
			int parent = -1;
			if (HasEntProp(ent, Prop_Data, "m_hMoveParent"))
				parent = GetEntPropEnt(ent, Prop_Data, "m_hMoveParent");
			LogMessage("[layout] near ent %d %s '%s' at (%.0f %.0f %.0f) parent %d",
				ent, class, name, pos[0], pos[1], pos[2], parent);
			found++;
		}
		LogMessage("[layout] near (%.0f %.0f %.0f) r%.0f: %d",
			centre[0], centre[1], centre[2], radius, found);
		return Plugin_Handled;
	}

	static const char classes[][] = {
		"obj_weapon_cache", "point_controlpoint", "trigger_capture_zone",
		"ins_spawnzone", "ins_blockzone"
	};
	for (int c = 0; c < sizeof(classes); c++)
	{
		int ent = -1, n = 0;
		while ((ent = FindEntityByClassname(ent, classes[c])) != -1)
		{
			char name[64];
			name[0] = '\0';
			if (HasEntProp(ent, Prop_Data, "m_iName"))
				GetEntPropString(ent, Prop_Data, "m_iName", name, sizeof(name));
			float pos[3];
			if (!GetEntityOrigin(ent, pos))
				continue;
			LogMessage("[layout] dump %s ent %d '%s' at (%.0f %.0f %.0f)",
				classes[c], ent, name, pos[0], pos[1], pos[2]);
			n++;
		}
		LogMessage("[layout] dump %s: %d", classes[c], n);
	}
	return Plugin_Handled;
}

public void OnMapStart()
{
	// The mesh is decorated off OnServerActivate and caches spawn from the
	// .txt, so neither is ready this early.
	CreateTimer(5.0, Timer_PostLoad);
}

/**
 * Runs on every load, the stock one included. A stock load has nothing to move
 * - but the map-wide hull sweep is the same measurement either way, and it is
 * the control every other load is read against. Skipping it on stock also left
 * a harness that waits for this line waiting forever on the one load that was
 * never going to print it.
 */
public Action Timer_PostLoad(Handle timer)
{
	if (!g_activeIsStock)
	{
		MoveObjectives();
		DisableBlockZones(true);
	}
	VerifyPlacements();
	return Plugin_Stop;
}

/**
 * A cache is the one thing here that is placed rather than rewritten: it comes
 * from maps/<map>.txt, which the applier cannot touch, so a moved cache is a
 * cache the game spawned where the .txt says and the plugin then teleported.
 * That holds only until something puts it back, and in checkpoint the round
 * does - each cache is its own round, and the round start resets them.
 *
 * Re-teleporting on every round start is idempotent: the preset carries
 * absolute origins and the same read-back check runs each time, so a round
 * that changed nothing logs the cache already standing where it was put.
 */
public void Event_RoundStart(Event event, const char[] name, bool dontBroadcast)
{
	// Before the stock return, because a stock map is a thing to announce too:
	// half of "was that layout bad?" is knowing when the answer is "that was
	// the map as it ships".
	AnnounceOnce();

	// Not gated on there being objectives to move: restricted areas ride the
	// same burst, and a preset can permute the ground without touching a cache.
	if (g_activeIsStock)
		return;
	// Not inline: the cache and its marker are still spawning when the event
	// fires, and a marker teleported inside its own spawn frame reads back on
	// the origin the map gave it - the move is made and then overwritten. Half
	// a second is after the round's entities have settled and still inside
	// freeze time, so nothing is ever seen in the wrong place.
	g_objPass = 0;
	CreateTimer(0.5, Timer_RoundObjectives, _, TIMER_REPEAT);
}

/**
 * Not one pass but a burst of them over the round's first seconds. A cache
 * takes the move as soon as it exists; its marker does not, and what puts the
 * marker back is the game finishing with it after we did. Rather than guess
 * that delay, the pass is repeated while the round settles - it is idempotent,
 * and a pass with nothing to do says nothing.
 */
public Action Timer_RoundObjectives(Handle timer)
{
	MoveObjectives(g_objPass == 0);
	DisableBlockZones(g_objPass == 0);
	return (++g_objPass < MM_ROUND_PASSES) ? Plugin_Continue : Plugin_Stop;
}

/**
 * Fire Disable at every restricted area, every pass.
 *
 * StartDisabled in the lump is not enough on its own: the checkpoint gamemode
 * enables the zone that belongs to the stage being fought, so a zone disabled
 * at load is switched back on by the round that needs it - and under a
 * permutation the side it holds is the wrong one, which is a warning on the
 * screen and a countdown for a player standing on their own route. The input
 * is idempotent, so it rides along with the objective burst rather than
 * needing a schedule of its own.
 */
static void DisableBlockZones(bool verbose)
{
	if (g_cvBlockZones.BoolValue)
		return;

	int n = 0, ent = -1;
	while ((ent = FindEntityByClassname(ent, "ins_blockzone")) != -1)
	{
		AcceptEntityInput(ent, "Disable");
		n++;
	}
	if (verbose && n > 0)
		LogMessage("[layout] %d restricted area(s) disabled", n);
}

// ===========================================================================
// Preset file
// ===========================================================================

// A mapcycle's spelling of a map name is not the file's. Six entries of the
// server list this was built against are capitalised - Hastings, Flakturm,
// Kunar, Marquis, SHELLSHOCK, Drycanal_coop_old - while every .bsp on disk and
// every preset the generator writes is lower case, because the generator names
// its output after the survey and the survey after the file. GetCurrentMap
// hands back whatever the cycle said, so on Linux, where the filesystem cares,
// five of those six would have found no preset and loaded stock without
// anything looking wrong: "this map has no preset file" is a sentence the
// applier says for a map that has one. (The sixth, Hastings, has no preset
// either way - it is one of the 22 maps in the cycle that were never surveyed.)
//
// So the exact name is tried first - a server whose presets are named to match
// its cycle keeps working - and the lower-cased name second.
static KeyValues LoadPresets(const char[] mapName)
{
	char dir[PLATFORM_MAX_PATH], path[PLATFORM_MAX_PATH];
	g_cvDir.GetString(dir, sizeof(dir));
	BuildPath(Path_SM, path, sizeof(path), "../../%s/%s.cfg", dir, mapName);
	if (!FileExists(path))
	{
		char lower[PLATFORM_MAX_PATH];
		strcopy(lower, sizeof(lower), mapName);
		for (int i = 0; lower[i] != '\0'; i++)
			lower[i] = CharToLower(lower[i]);

		if (StrEqual(lower, mapName))
			return null;

		BuildPath(Path_SM, path, sizeof(path), "../../%s/%s.cfg", dir, lower);
		if (!FileExists(path))
			return null;

		LogMessage("[layout] %s: preset read from %s.cfg - the map name is cased "
			... "differently in the mapcycle than on disk", mapName, lower);
	}

	KeyValues kv = new KeyValues("layout");
	if (!kv.ImportFromFile(path))
	{
		LogError("[layout] could not parse %s", path);
		delete kv;
		return null;
	}
	return kv;
}

static int ListPresets(KeyValues kv, char[][] names, int maxNames)
{
	kv.Rewind();
	if (!kv.GotoFirstSubKey())
		return 0;

	int n = 0;
	do
	{
		char section[64];
		kv.GetSectionName(section, sizeof(section));
		if (!StrEqual(section, "preset", false))
			continue;
		char name[64];
		kv.GetString("name", name, sizeof(name), "");
		if (name[0] == '\0' || n >= maxNames)
			continue;
		strcopy(names[n++], 64, name);
	}
	while (kv.GotoNextKey());

	kv.Rewind();
	return n;
}

static bool SelectPreset(KeyValues kv, const char[] want)
{
	kv.Rewind();
	if (!kv.GotoFirstSubKey())
		return false;
	do
	{
		char section[64];
		kv.GetSectionName(section, sizeof(section));
		if (!StrEqual(section, "preset", false))
			continue;
		char name[64];
		kv.GetString("name", name, sizeof(name), "");
		if (StrEqual(name, want, false))
			return true;
	}
	while (kv.GotoNextKey());
	return false;
}

/**
 * Round-robin, not random: players notice repeats far more than they notice a
 * rotation. The index is persisted per map so a server restart does not reset
 * everyone to preset 0.
 */
static int ChoosePreset(const char[] mapName, char[][] names, int n)
{
	if (g_pinned[0] != '\0')
	{
		for (int i = 0; i < n; i++)
			if (StrEqual(names[i], g_pinned, false))
				return i;
		LogMessage("[layout] pinned preset '%s' not in %s - rotating instead", g_pinned, mapName);
	}

	char path[PLATFORM_MAX_PATH];
	BuildPath(Path_SM, path, sizeof(path), "data/mapmaker_rotation.txt");

	KeyValues state = new KeyValues("rotation");
	if (FileExists(path))
		state.ImportFromFile(path);

	int last = state.GetNum(mapName, -1);
	int next = (last + 1) % n;
	state.SetNum(mapName, next);

	state.Rewind();
	state.ExportToFile(path);
	delete state;

	return next;
}

// ===========================================================================
// Entity-lump rewriting
// ===========================================================================

/**
 * Read the preset's edit rules into memory. No lump access here.
 */
static void LoadEdits(KeyValues kv)
{
	g_eCount = 0;
	if (!kv.JumpToKey("edit"))
		return;
	if (!kv.GotoFirstSubKey())
	{
		kv.GoBack();
		return;
	}

	do
	{
		if (g_eCount >= MM_MAX_EDITS)
		{
			LogError("[layout] preset has more than %d edits - the rest are ignored",
				MM_MAX_EDITS);
			break;
		}

		char within[128], offsetStr[64];
		kv.GetString("class", g_eClass[g_eCount], sizeof(g_eClass[]), "");
		kv.GetString("target", g_eTarget[g_eCount], sizeof(g_eTarget[]), "");
		kv.GetString("origin", g_eOrigin[g_eCount], sizeof(g_eOrigin[]), "");
		kv.GetString("within", within, sizeof(within), "");
		kv.GetString("offset", offsetStr, sizeof(offsetStr), "");
		kv.GetString("rename", g_eRename[g_eCount], sizeof(g_eRename[]), "");
		g_eTeam[g_eCount] = kv.GetNum("team", -1);

		if (g_eClass[g_eCount][0] == '\0')
			continue;
		// A rule with no geometry and no new name would match entries and do
		// nothing to them - and it would still consume this entry's one edit.
		if (g_eOrigin[g_eCount][0] == '\0' && offsetStr[0] == '\0'
		 && g_eRename[g_eCount][0] == '\0')
			continue;

		g_eHasBox[g_eCount] = (within[0] != '\0')
			&& ParseFloats(within, g_eBox[g_eCount], 6);
		g_eHasOffset[g_eCount] = (offsetStr[0] != '\0')
			&& ParseFloats(offsetStr, g_eOffset[g_eCount], 3);
		g_eMatches[g_eCount] = 0;
		g_eShadowed[g_eCount] = 0;
		g_eOrdinal[g_eCount] = -1;
		g_eNoOrigin[g_eCount] = false;
		g_eVerdict[g_eCount] = MMV_UNMATCHED;
		g_eEntity[g_eCount] = -1;
		g_eOffBy[g_eCount] = -1.0;
		g_eCount++;
	}
	while (kv.GotoNextKey());

	kv.GoBack();
	kv.GoBack();
}

/**
 * Rewrite the entity lump in one pass, testing every entry against every rule.
 *
 * vbsp stores a brush entity's geometry relative to its origin key and the
 * engine re-applies it at load - the same reason a func_door can move - so
 * changing the key translates the volume. Volumes translate; they do not
 * resize. Widening a zone means borrowing another brush's model key, which is
 * why the survey exports model bounds.
 *
 * Spawn points bind to a zone by containment, not by name (all 442 of
 * ministry_coop's fall inside a same-team zone, with no name reference), so a
 * moved zone needs the points inside it moved with it - and they carry no
 * targetname, so a rule identifies one by the origin it has *before* the edit.
 * That is the "within" box, and a rule that also carries an absolute "origin"
 * is idempotent: two rules matching one entry cannot compound the way two
 * offsets would.
 */
static void ApplyEdits()
{
	if (g_eCount == 0)
		return;

	// One server setting, read once, not once per entry.
	bool blockZones = g_cvBlockZones.BoolValue;
	g_blockZonesOff = 0;
	g_blockZonesUnbound = 0;

	delete g_lumpClassCount;
	g_lumpClassCount = new StringMap();

	int count = EntityLump.Length();
	for (int i = 0; i < count; i++)
	{
		EntityLumpEntry entry = EntityLump.Get(i);
		if (entry == null)
			continue;

		char class[64];
		entry.GetNextKey("classname", class, sizeof(class));
		if (class[0] == '\0')
		{
			delete entry;
			continue;
		}

		// Disabled rather than erased: an erased entry would renumber every
		// entry after it, and the verification pass reads a rule back by its
		// place in the lump. A disabled zone still spawns and still counts.
		if (!blockZones && StrEqual(class, "ins_blockzone", false))
		{
			char cur[8];
			int di = entry.GetNextKey("StartDisabled", cur, sizeof(cur));
			if (di == -1)
				entry.Append("StartDisabled", "1");
			else
				entry.Update(di, NULL_STRING, "1");
			g_blockZonesOff++;
		}

		// StartDisabled alone does not hold: CINSSpawnZone::ToggleBlockzone
		// re-enables the volumes its own stage names, every time that stage
		// goes live, which is why a zone switched off at load comes back the
		// moment the round advances. With the key gone the name is empty and
		// the toggle returns early - the same edit tools/blockzones.py makes
		// in the file, made here in the lump instead.
		if (!blockZones && StrEqual(class, "ins_spawnzone", false))
		{
			char cur[64];
			int bi = entry.GetNextKey("blockzone", cur, sizeof(cur));
			if (bi != -1)
			{
				entry.Erase(bi);
				g_blockZonesUnbound++;
			}
		}

		// This entry's place in its class, counted over every entry of that
		// class whether or not a rule wants it and whether or not it carries
		// an origin - because the map will spawn all of them, in this order,
		// and that ordering is the only handle from a rule back to the entity
		// its entry became. Position is not: a preset that permutes 442 spawn
		// points among each other leaves an entity on every target coordinate
		// even if not one of them moved.
		int ordinal = 0;
		g_lumpClassCount.GetValue(class, ordinal);
		g_lumpClassCount.SetValue(class, ordinal + 1);

		// Read targetname, team and origin once each, not once per rule.
		char target[64], teamStr[32], value[128];
		entry.GetNextKey("targetname", target, sizeof(target));
		entry.GetNextKey("TeamNum", teamStr, sizeof(teamStr));
		if (teamStr[0] == '\0')
			entry.GetNextKey("teamnum", teamStr, sizeof(teamStr));
		int team = (teamStr[0] == '\0') ? -1 : StringToInt(teamStr);

		// A brush entity keeps its position in its model, and a mapper may
		// leave the origin key off altogether: every one of
		// cs_officeb3_coop_v1_5's 27 ins_spawnzone entries does, where all 22
		// of ministry_coop's carry one. Such an entry cannot be moved and
		// cannot be matched on a coordinate - but it can still be *renamed*,
		// which since docs/reverse.md §3 is what all but two clusters of a
		// layout are. Dropping the entry outright cost that map its whole
		// family: every zone rule read back unmatched on all 13 layouts. So a
		// missing key now disqualifies the entry only from the rules that
		// need one.
		int idx = entry.FindKey("origin");
		float o[3];
		bool hasOrigin = false;
		if (idx != -1)
		{
			entry.Get(idx, .valbuf = value, .vallen = sizeof(value));
			if (!ParseFloats(value, o, 3))
			{
				g_editsSkipped++;
				delete entry;
				continue;
			}
			hasOrigin = true;
		}

		bool edited = false;
		for (int r = 0; r < g_eCount; r++)
		{
			if (!RuleMatches(r, class, target, team, o, hasOrigin))
				continue;

			bool wantsMove = g_eHasOffset[r] || g_eOrigin[r][0] != '\0';

			// An offset has nothing to add to, and an absolute origin written
			// onto a brush that never had one translates it by the whole
			// coordinate rather than to it. Left unmatched on purpose: the
			// rule is then loud in the read-back rather than quietly doing
			// only the half of its job that was reachable.
			if (wantsMove && !hasOrigin)
			{
				g_editsSkipped++;
				continue;
			}

			// One edit per entity. A second matching rule would be applying a
			// move to a position the first rule already chose - but it is
			// counted rather than passed over in silence, because "a later
			// rule wanted this entry too" and "no entry matched this rule"
			// arrive at the verification pass as the same nothing.
			if (edited)
			{
				g_eShadowed[r]++;
				continue;
			}
			edited = true;

			char newVal[64];
			bool moved = wantsMove;
			if (g_eHasOffset[r])
				Format(newVal, sizeof(newVal), "%.2f %.2f %.2f",
					o[0] + g_eOffset[r][0], o[1] + g_eOffset[r][1],
					o[2] + g_eOffset[r][2]);
			else
				strcopy(newVal, sizeof(newVal), g_eOrigin[r]);

			if (moved)
				entry.Update(idx, NULL_STRING, newVal);

			// A rename is how a stage is given the ground the map already
			// authored for its rung: the cpsetup binds a stage to a targetname,
			// so handing that name to the volume already standing there moves
			// nothing at all. Safe as a cycle - A gets B's name while B gets
			// C's - because the walk visits each entry once and edits it at
			// its first matching rule only, against a targetname read before
			// any edit, so an entry this pass has renamed is never re-read by
			// the rule for its new name.
			if (g_eRename[r][0] != '\0')
			{
				int tidx = entry.FindKey("targetname");
				if (tidx == -1)
					entry.Append("targetname", g_eRename[r]);
				else
					entry.Update(tidx, NULL_STRING, g_eRename[r]);
			}
			// What this rule did, for the read-back in VerifyRules. A rule
			// that only renames still records where its entity stands, so the
			// new name can be checked against the entity meant to carry it.
			g_eMatches[r]++;
			g_eOrdinal[r] = ordinal;
			g_eNoOrigin[r] = !hasOrigin;
			g_eWas[r] = o;
			if (!moved || !ParseFloats(newVal, g_eIntended[r], 3))
				g_eIntended[r] = o;

			g_editsApplied++;

			if (g_cvDebug.BoolValue)
				LogMessage("[layout]   %s%s%s: %s%s%s%s", class,
					target[0] != '\0' ? "/" : "", target,
					moved ? value : "", moved ? " -> " : "", moved ? newVal : "",
					g_eRename[r][0] != '\0' ? g_eRename[r] : "");
		}

		delete entry;
	}
}

/**
 * Does this rule claim this lump entry? Split out of the walk so that the
 * shadowing count above is the same test the edit itself made, rather than a
 * second copy of it that can drift.
 */
static bool RuleMatches(int r, const char[] class, const char[] target, int team,
	const float o[3], bool hasOrigin)
{
	if (!StrEqual(class, g_eClass[r], false))
		return false;
	if (g_eTarget[r][0] != '\0' && !StrEqual(target, g_eTarget[r], false))
		return false;
	if (g_eTeam[r] >= 0 && team != g_eTeam[r])
		return false;
	// A `within` box is a question about a coordinate, and an entry with no
	// origin key has none to answer it with.
	if (g_eHasBox[r] && !hasOrigin)
		return false;
	if (g_eHasBox[r]
	 && (o[0] < g_eBox[r][0] || o[1] < g_eBox[r][1] || o[2] < g_eBox[r][2]
	  || o[0] > g_eBox[r][3] || o[1] > g_eBox[r][4] || o[2] > g_eBox[r][5]))
		return false;
	return true;
}

static bool ParseFloats(const char[] s, float[] out, int count)
{
	// The preset format pads for readability - a "within" box is written as
	// "minx miny minz  maxx maxy maxz", two spaces between the halves - and
	// ExplodeString returns an empty token for every run of separators. Empty
	// tokens are skipped, so any spacing parses to the same six floats.
	char parts[16][32];
	int n = ExplodeString(s, " ", parts, sizeof(parts), sizeof(parts[]));
	int got = 0;
	for (int i = 0; i < n && got < count; i++)
	{
		if (parts[i][0] == '\0')
			continue;
		out[got++] = StringToFloat(parts[i]);
	}
	return got == count;
}

// ===========================================================================
// Objectives (caches live in maps/<name>.txt, not the lump)
// ===========================================================================

static void CollectObjectives(KeyValues kv)
{
	if (!kv.JumpToKey("objective"))
		return;
	if (!kv.GotoFirstSubKey())
	{
		kv.GoBack();
		return;
	}
	do
	{
		if (g_objCount >= sizeof(g_objName))
			break;
		char name[64], cp[64], originStr[64];
		kv.GetString("name", name, sizeof(name), "");
		kv.GetString("origin", originStr, sizeof(originStr), "");
		float o[3];
		if (name[0] == '\0' || !ParseFloats(originStr, o, 3))
			continue;
		// The control point's targetname, from the preset. It cannot be derived:
		// ministry_coop's cache_a is marked by cachepoint_a, not cache_a_cp, and
		// guessing the suffix silently leaves every marker behind its cache.
		kv.GetString("cp", cp, sizeof(cp), "");
		strcopy(g_objName[g_objCount], 64, name);
		strcopy(g_objCp[g_objCount], 64, cp);
		g_objOrigin[g_objCount] = o;
		// Optional, and absent from a preset written before they existed: a
		// missing hint means the first entity of that name, which is what the
		// applier did for every preset up to now.
		char fromStr[64];
		kv.GetString("from", fromStr, sizeof(fromStr), "");
		g_objHasFrom[g_objCount] = ParseFloats(fromStr, g_objFrom[g_objCount], 3);
		kv.GetString("cp_from", fromStr, sizeof(fromStr), "");
		g_objHasCpFrom[g_objCount] = ParseFloats(fromStr, g_objCpFrom[g_objCount], 3);
		// The shipped convention: the control point marker sits +72 above the
		// cache it belongs to.
		g_objCpOffset[g_objCount] = kv.GetFloat("cp_offset", 72.0);
		g_objCount++;
	}
	while (kv.GotoNextKey());
	kv.GoBack();
	kv.GoBack();
}

/**
 * Move a control point on the *map screen*, which is a different thing from
 * moving it in the world.
 *
 * The HUD, the compass and the minimap do not read the marker entity. The
 * control point master copies every point's origin into the objective
 * resource's networked m_vCPPositions array during setup - once, at level init
 * - and the client draws from that array. A marker rewritten in the entity
 * lump is therefore already right, because the lump is rewritten before any
 * entity exists and setup reads the new origin. A cache marker is not: a cache
 * comes from maps/<map>.txt and can only be teleported after setup has run, so
 * the world says one thing and every map screen says another.
 *
 * This build's point_controlpoint carries no point index to read, so the slot
 * is found by the coordinate the array still holds: the entry standing on the
 * marker's old position is the entry that describes that marker. Returns the
 * slot it wrote, -1 if it found nothing to write.
 */
static int UpdateCPMarker(const float from[3], const float to[3])
{
	int res = FindEntityByClassname(-1, "ins_objective_resource");
	if (res == -1)
		return -1;

	int n = GetEntProp(res, Prop_Send, "m_iNumControlPoints");
	if (n <= 0 || n > MM_MAX_CONTROL_POINTS)
		n = MM_MAX_CONTROL_POINTS;

	for (int i = 0; i < n; i++)
	{
		float pos[3];
		GetEntPropVector(res, Prop_Send, "m_vCPPositions", pos, i);
		// Already carrying this move - a later pass over the same round.
		if (GetVectorDistance(pos, to) <= MM_VERIFY_TOL)
			return i;
		if (GetVectorDistance(pos, from) <= MM_VERIFY_TOL)
		{
			SetEntPropVector(res, Prop_Send, "m_vCPPositions", to, i);
			return i;
		}
	}
	return -1;
}

static void MoveObjectives(bool verbose = true)
{
	// Every pass re-answers the same question, so it starts from no failures
	// rather than adding this pass's to the last one's.
	g_objFailed = 0;

	for (int i = 0; i < g_objCount; i++)
	{
		int ent = FindObjectiveEntity(g_objName[i], g_objHasFrom[i], g_objFrom[i],
			g_objOrigin[i], "objective", "obj_weapon_cache");
		if (ent == -1)
		{
			g_objFailed++;
			LogError("[layout] objective '%s': no entity by that name in the "
				... "running map - preset may be stale", g_objName[i]);
			continue;
		}

		// Where it was standing before this pass. On the first pass that is
		// the .txt coordinate; on a later one it says whether the game put the
		// cache back, which is the only way to tell a reset from a no-op.
		float before[3];
		bool hadBefore = GetEntityOrigin(ent, before);

		TeleportEntity(ent, g_objOrigin[i], NULL_VECTOR, NULL_VECTOR);

		// Read the position back rather than trust the call.
		// CObjWeaponCache::Teleport is overridden - that override is the reason
		// a cache can be moved at all, since it carries the cache's own trigger
		// with it - and an override is exactly the kind of thing that can
		// decline a move, clamp it, or move only half of itself.
		float landed[3];
		if (!GetEntityOrigin(ent, landed))
		{
			g_objFailed++;
			LogError("[layout] objective '%s': ent %d has no origin to read back",
				g_objName[i], ent);
		}
		else if (GetVectorDistance(landed, g_objOrigin[i]) > MM_VERIFY_TOL)
		{
			g_objFailed++;
			LogError("[layout] objective '%s': ent %d asked for (%.0f %.0f %.0f), "
				... "sits at (%.0f %.0f %.0f)", g_objName[i], ent,
				g_objOrigin[i][0], g_objOrigin[i][1], g_objOrigin[i][2],
				landed[0], landed[1], landed[2]);
		}
		else if (hadBefore && GetVectorDistance(before, landed) <= MM_VERIFY_TOL)
		{
			// Nothing to do. Said once per round, not once per pass.
			if (verbose)
				LogMessage("[layout] objective '%s': ent %d already at (%.0f %.0f %.0f)",
					g_objName[i], ent, landed[0], landed[1], landed[2]);
		}
		else
		{
			LogMessage("[layout] objective '%s': ent %d moved to (%.0f %.0f %.0f) "
				... "from (%.0f %.0f %.0f)", g_objName[i], ent,
				landed[0], landed[1], landed[2],
				before[0], before[1], before[2]);
		}

		// The marker follows its cache, by the map's own offset. Which entity
		// answered to the name is logged because the name cannot be derived -
		// ministry_coop's cache_a is marked by cachepoint_a - and a preset
		// naming the wrong one leaves the marker behind its cache in silence.
		char cpName[80];
		if (g_objCp[i][0] != '\0')
			strcopy(cpName, sizeof(cpName), g_objCp[i]);
		else
			Format(cpName, sizeof(cpName), "%s_cp", g_objName[i]);

		float cpWant[3];
		cpWant = g_objOrigin[i];
		cpWant[2] += g_objCpOffset[i];
		int cp = FindObjectiveEntity(cpName, g_objHasCpFrom[i], g_objCpFrom[i],
			cpWant, "control point", "point_controlpoint");
		if (cp == -1)
		{
			g_objFailed++;
			LogError("[layout] objective '%s': control point '%s' not found - "
				... "the marker stays where the stock map put it", g_objName[i], cpName);
			continue;
		}

		float cpPos[3], cpBefore[3];
		cpPos = cpWant;
		GetEntityOrigin(cp, cpBefore);
		TeleportEntity(cp, cpPos, NULL_VECTOR, NULL_VECTOR);

		char cpClass[64];
		GetEntityClassname(cp, cpClass, sizeof(cpClass));

		// The map screen is a separate answer from the world, and it is the one
		// a player reads: the client draws every marker from the objective
		// resource's networked array, not from this entity. So the array is
		// written whether or not the entity took the move.
		int slot = UpdateCPMarker(cpBefore, cpPos);

		float cpLanded[3];
		if (!GetEntityOrigin(cp, cpLanded))
		{
			g_objFailed++;
			LogError("[layout] objective '%s': control point '%s' (%s, ent %d) has "
				... "no origin to read back", g_objName[i], cpName, cpClass, cp);
		}
		else if (GetVectorDistance(cpLanded, cpPos) > MM_VERIFY_TOL)
		{
			g_objFailed++;
			// Where it ended up, not just that it is not where it was asked to
			// be: a marker that is carried by its cache lands on the cache and
			// needs no move at all, and one left behind lands on the stock
			// coordinate. Those are different faults and the position is what
			// tells them apart. The pass says whether a later one recovered it.
			LogError("[layout] objective '%s': control point '%s' (%s, ent %d) asked "
				... "for (%.0f %.0f %.0f), sits at (%.0f %.0f %.0f) - pass %d, "
				... "minimap slot %d",
				g_objName[i], cpName, cpClass, cp,
				cpPos[0], cpPos[1], cpPos[2],
				cpLanded[0], cpLanded[1], cpLanded[2], g_objPass, slot);
		}
		else if (verbose || GetVectorDistance(cpBefore, cpLanded) > MM_VERIFY_TOL)
		{
			LogMessage("[layout]   cp '%s' (%s, ent %d) moved to (%.0f %.0f %.0f), "
				... "minimap slot %d", cpName, cpClass, cp,
				cpLanded[0], cpLanded[1], cpLanded[2], slot);
		}
	}
}

/**
 * The entity of this name that the preset means.
 *
 * `from` is where it stands on the stock map and `to` is where this preset
 * puts it; the match is the candidate nearest to *either*, because a later
 * pass over the same round finds the real one already moved and the decoy
 * still standing where it always was. With no hint this is the first match,
 * which is what the plugin did before the hint existed. A name that answers
 * more than once is logged whichever way it resolves: it is the kind of thing
 * that is invisible until a marker is 1,696 u from its cache.
 *
 * **Class before distance.** A map can give the cache and its marker one name
 * and one coordinate - baghdad_remastered's cachepoint7 is an obj_weapon_cache
 * and a point_controlpoint both at (-2377 -2410 -199) - and then distance is a
 * tie that entity order breaks. The cache lookup took the marker, so the marker
 * went to the new rung and the cache stayed on the stock one. So a candidate
 * of the class the caller wants beats any other, and distance only chooses
 * among equals. Another class is still taken when nothing of `wantClass`
 * answers: a marker carried by its cache is found as the cache.
 */
static int FindObjectiveEntity(const char[] name, bool hasFrom,
	const float from[3], const float to[3], const char[] what,
	const char[] wantClass)
{
	int best = -1, matches = 0;
	bool bestWanted = false;
	float bestScore = 0.0;
	int ent = -1;
	while ((ent = FindEntityByClassname(ent, "*")) != -1)
	{
		if (!HasEntProp(ent, Prop_Data, "m_iName"))
			continue;
		char n[64];
		GetEntPropString(ent, Prop_Data, "m_iName", n, sizeof(n));
		if (!StrEqual(n, name, false))
			continue;
		matches++;

		float here[3];
		float score = 0.0;
		if (hasFrom && GetEntityOrigin(ent, here))
		{
			score = GetVectorDistance(here, from);
			float moved = GetVectorDistance(here, to);
			if (moved < score)
				score = moved;
		}
		char cls[64];
		GetEntityClassname(ent, cls, sizeof(cls));
		bool wanted = StrEqual(cls, wantClass, false);
		if (best == -1 || (wanted && !bestWanted)
			|| (wanted == bestWanted && score < bestScore))
		{
			best = ent;
			bestWanted = wanted;
			bestScore = score;
		}
		if (!hasFrom && wanted)
			break;
	}

	if (matches > 1)
		LogMessage("[layout] %s '%s': %d entities answer to that name; took ent %d "
			... "(%s) at %.0f u from the coordinate the preset was measured at",
			what, name, matches, best, bestWanted ? wantClass : "another class",
			bestScore);
	return best;
}

// ===========================================================================
// Reading the running map
// ===========================================================================
//
// The same three readers the survey plugin uses, and deliberately the same
// shape: what this plugin checks after a load has to be the thing the survey
// measured before it, or a disagreement between them is a difference in the
// reader rather than in the map.

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

/** Collision bounds, which for a brush entity are relative to its origin. */
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

/** The same shape as CINSRules::IsSpawnPointValid: a player hull, in place. */
static bool HullFits(float pos[3], float mins[3], float maxs[3])
{
	TR_TraceHull(pos, pos, mins, maxs, MASK_PLAYERSOLID);
	return !TR_DidHit() && !TR_StartSolid();
}

// ===========================================================================
// The gate
// ===========================================================================

/**
 * The lump is rewritten before the collision world exists, so nothing can be
 * tested at apply time - it is tested here instead, after the map is up.
 *
 * That means this reports rather than prevents. CINSRules::ValidateSpawnpoints
 * does the same: it iterates teams 2-3, Msg's each failure and removes nothing.
 * Rejection bites at selection time, so a handful of bad points costs spawn
 * choices, not a broken map. What matters is that a stale preset is loud.
 *
 * Two passes, because they answer different questions. VerifyRules reads each
 * rule back and says which mechanism failed for it. The map-wide sweep counts
 * every spawn point on the map, the ones no rule touched included, and that is
 * the number comparable to stock: ministry_coop logs exactly one rejection
 * unmodified, so anything above that is ours.
 */
static void VerifyPlacements()
{
	float hullMin[3], hullMax[3];
	ParseConVarVector(g_cvHullMin, hullMin);
	ParseConVarVector(g_cvHullMax, hullMax);

	int hullFailed = 0;
	int broken = VerifyRules(hullMin, hullMax, hullFailed);

	int checked = 0, failed = 0, ent = -1;
	while ((ent = FindEntityByClassname(ent, "ins_spawnpoint")) != -1)
	{
		float o[3];
		if (!GetEntityOrigin(ent, o))
			continue;
		checked++;
		if (!HullFits(o, hullMin, hullMax))
			failed++;
	}
	LogMessage("[layout] verify: map-wide %d of %d spawn points fit the player hull",
		checked - failed, checked);

	int limit = g_cvMaxFailures.IntValue;
	if (broken > 0 || g_objFailed > 0 || failed > limit)
		LogError("[layout] preset '%s': %d rule(s) did not take, %d objective "
			... "failure(s), %d of %d spawn points fail the hull test (limit %d). "
			... "A rule that did not take means this preset does not describe this "
			... "map any more - re-survey it.",
			g_activeName, broken, g_objFailed, failed, checked, limit);
	else
		LogMessage("[layout] preset '%s' verified: %d rules took, %d of them on ground "
			... "no player fits, %d objectives",
			g_activeName, g_eCount, hullFailed, g_objCount);
}

/**
 * Read every rule back off the running map.
 *
 * A rule is matched to its entity by lump ordinal, not by position: the map
 * spawns the lump in order, so the k-th entity of a class is the k-th entry of
 * that class, and the coordinate is then something to *test* rather than the
 * thing being looked up. Position alone cannot do this job - the presets are
 * permutations, and a permutation that did not happen leaves an entity sitting
 * on every coordinate the preset asked for.
 *
 * Returns the number of rules that did not take. Three outcomes are reported
 * without counting as that:
 *
 *   hull     - comes back in hullFailed. A point nobody fits on is a
 *              coordinate the generator chose badly, not a mechanism that
 *              failed, and the two have different fixes.
 *   shadowed - two rules describing one entry is a redundancy in the
 *              generator; erroring on it every load would teach whoever reads
 *              this log to stop reading it.
 *   nudged   - the edit took and the engine then adjusted the entity. Worth
 *              seeing, and not a failure of anything here.
 *
 * One thing this pass cannot see: the walk in ApplyEdits skips a lump entry
 * that carries no origin key at all, so a rule aimed at one reads back as
 * unmatched. That is the right answer rather than a blind spot - vbsp stores a
 * brush relative to the origin key it was given, so an entity that never had
 * one would be *translated by* a new key rather than moved to it.
 */
static int VerifyRules(float hullMin[3], float hullMax[3], int &hullFailed)
{
	hullFailed = 0;
	if (g_eCount == 0)
		return 0;

	// Distinct classes, so the entity list is walked once per class rather
	// than once per rule: a reversal is 444 rules over five classes.
	char classes[MM_MAX_VERIFY_CLASSES][32];
	int nclasses = 0;
	for (int r = 0; r < g_eCount; r++)
	{
		bool seen = false;
		for (int c = 0; c < nclasses; c++)
		{
			if (StrEqual(classes[c], g_eClass[r], false))
			{
				seen = true;
				break;
			}
		}
		if (seen)
			continue;
		if (nclasses >= MM_MAX_VERIFY_CLASSES)
		{
			LogError("[layout] preset touches more than %d classes - the rest go "
				... "unverified", MM_MAX_VERIFY_CLASSES);
			break;
		}
		strcopy(classes[nclasses++], sizeof(classes[]), g_eClass[r]);
	}

	int broken = 0, shown = 0, hidden = 0;

	for (int c = 0; c < nclasses; c++)
	{
		int nents = CollectClass(classes[c]);

		// The ordinals only mean anything if the map spawned as many of these
		// as the lump described. When it did not, every verdict for the class
		// is off by however many went missing, and saying so is the difference
		// between a wrong answer and a withheld one.
		int inLump = -1;
		if (g_lumpClassCount != null)
			g_lumpClassCount.GetValue(classes[c], inLump);
		bool aligned = (inLump == nents);
		if (!aligned)
			LogError("[layout] verify %s: lump held %d, map spawned %d - entries and "
				... "entities do not line up, so the verdicts below are unreliable",
				classes[c], inLump, nents);

		bool hullMatters = StrEqual(classes[c], "ins_spawnpoint", false);

		int tally[MMV_COUNT];
		for (int v = 0; v < MMV_COUNT; v++)
			tally[v] = 0;
		int rules = 0;

		for (int r = 0; r < g_eCount; r++)
		{
			if (!StrEqual(g_eClass[r], classes[c], false))
				continue;

			rules++;
			int v = VerifyRule(r, nents, hullMatters, hullMin, hullMax);
			g_eVerdict[r] = v;
			tally[v]++;

			if (v == MMV_HULL)
				hullFailed++;
			else if (v != MMV_OK && v != MMV_SHADOWED && v != MMV_NUDGED && aligned)
				broken++;

			if (v == MMV_OK && !g_cvDebug.BoolValue)
				continue;
			if (g_cvDebug.BoolValue || shown < MM_VERIFY_DETAIL)
			{
				LogRuleDetail(r);
				shown++;
			}
			else
				hidden++;
		}

		// Built off g_verdict so the labels cannot drift from the counts.
		char summary[256], one[32];
		summary[0] = '\0';
		for (int v = 0; v < MMV_COUNT; v++)
		{
			Format(one, sizeof(one), "%s=%d ", g_verdict[v], tally[v]);
			StrCat(summary, sizeof(summary), one);
		}
		LogMessage("[layout] verify %s: %d rules over %d entities - %s",
			classes[c], rules, nents, summary);
	}

	if (hidden > 0)
		LogMessage("[layout] verify: %d further rule(s) not listed - mm_layout_debug 1 "
			... "lists every one", hidden);

	return broken;
}

/** Whether this rule asked for a name the entity is not carrying. */
static bool RenameStuck(int r, int ent)
{
	if (g_eRename[r][0] == '\0')
		return false;
	char name[64];
	name[0] = '\0';
	if (HasEntProp(ent, Prop_Data, "m_iName"))
		GetEntPropString(ent, Prop_Data, "m_iName", name, sizeof(name));
	return !StrEqual(name, g_eRename[r], false);
}

static int VerifyRule(int r, int nents, bool hullMatters, float hullMin[3], float hullMax[3])
{
	g_eEntity[r] = -1;
	g_eOffBy[r] = -1.0;

	if (g_eMatches[r] == 0)
		return g_eShadowed[r] > 0 ? MMV_SHADOWED : MMV_UNMATCHED;

	// The entry this rule last edited, as an entity.
	if (g_eOrdinal[r] < 0 || g_eOrdinal[r] >= nents)
		return MMV_MISSING;

	int ent = g_vEnt[g_eOrdinal[r]];
	g_eEntity[r] = ent;

	// An entry with no origin key was renamed and nothing else, and neither
	// question below is about it: there is no intended coordinate to have
	// missed, and `adrift` measures a volume against an origin this entity
	// does not have - it would read every such zone as adrift of (0 0 0).
	// What is left to check is the only thing the rule asked for, the name.
	if (g_eNoOrigin[r])
	{
		g_eOffBy[r] = 0.0;
		return RenameStuck(r, ent) ? MMV_MISNAMED : MMV_OK;
	}

	g_eOffBy[r] = GetVectorDistance(g_vPos[g_eOrdinal[r]], g_eIntended[r]);

	if (g_eOffBy[r] > MM_VERIFY_TOL)
	{
		// Still on the coordinate the rule replaced: the origin was written
		// into the lump and the map came up as though it had not been.
		if (GetVectorDistance(g_vPos[g_eOrdinal[r]], g_eWas[r]) <= MM_VERIFY_TOL)
			return MMV_STUCK;
		return g_eOffBy[r] <= MM_VERIFY_NEAR ? MMV_NUDGED : MMV_MISSING;
	}

	if (RenameStuck(r, ent))
		return MMV_MISNAMED;

	if (VolumeAdrift(ent))
		return MMV_ADRIFT;

	if (hullMatters && !HullFits(g_vPos[g_eOrdinal[r]], hullMin, hullMax))
		return MMV_HULL;

	return MMV_OK;
}

/**
 * Whether a brush entity's volume is anywhere near the origin it was given.
 *
 * vbsp stores brush geometry relative to the origin key, so writing a new key
 * translates the volume. That is the whole mechanism, and the file route
 * confirmed it in game for ins_spawnzone - but not for trigger_capture_zone,
 * and the way it would fail is silent: the entity reports the new origin while
 * its collision volume stays where it was authored.
 *
 * Nothing here can say a volume is in the *right* place; that needs the stock
 * bounds, which the plugin does not carry. It can say the volume is not around
 * its own origin, which is what a volume left behind looks like, because
 * collision bounds are stored relative to the origin. If some build ever
 * reports them in world space instead, every brush rule goes adrift at once and
 * the figures in the detail line say so plainly.
 */
static bool VolumeAdrift(int ent)
{
	float mins[3], maxs[3];
	if (!GetEntityBounds(ent, mins, maxs))
		return false;   // a point entity has no volume to leave behind

	float offset[3], half[3];
	for (int i = 0; i < 3; i++)
	{
		offset[i] = (mins[i] + maxs[i]) * 0.5;
		half[i] = (maxs[i] - mins[i]) * 0.5;
	}
	// An origin on the volume's own edge is ordinary: an origin brush sits
	// wherever the author put it. An origin further out than the volume's own
	// size is not.
	return GetVectorLength(offset) > GetVectorLength(half) + MM_VERIFY_TOL;
}

/**
 * Every entity of a class, in entity-index order - which is the order the map
 * spawned them, which is lump order. Nothing is filtered out of the list, so
 * index k stays the k-th entry of the class even if one of them turns out to
 * have no readable origin.
 */
static int CollectClass(const char[] class)
{
	int n = 0, ent = -1;
	while ((ent = FindEntityByClassname(ent, class)) != -1)
	{
		if (n >= MM_MAX_VERIFY_ENTS)
		{
			LogError("[layout] more than %d %s on this map - the rest go unverified",
				MM_MAX_VERIFY_ENTS, class);
			break;
		}
		if (!GetEntityOrigin(ent, g_vPos[n]))
			g_vPos[n][0] = g_vPos[n][1] = g_vPos[n][2] = 0.0;
		g_vEnt[n++] = ent;
	}
	return n;
}

/** Rule numbers are the order they were read in, not the preset's key names. */
static void LogRuleDetail(int r)
{
	if (g_eMatches[r] == 0)
	{
		// No before and after to print. What the rule was looking for is the
		// useful thing instead, since that is what has to be re-generated.
		char box[96];
		box[0] = '\0';
		if (g_eHasBox[r])
			Format(box, sizeof(box), " within (%.0f %.0f %.0f)..(%.0f %.0f %.0f)",
				g_eBox[r][0], g_eBox[r][1], g_eBox[r][2],
				g_eBox[r][3], g_eBox[r][4], g_eBox[r][5]);
		LogMessage("[layout]   rule %d %s%s%s team %d: %s - shadowed %d, wanted%s",
			r + 1, g_eClass[r], g_eTarget[r][0] != '\0' ? "/" : "", g_eTarget[r],
			g_eTeam[r], g_verdict[g_eVerdict[r]], g_eShadowed[r],
			box[0] != '\0' ? box : " any of that class");
		return;
	}

	char vol[96];
	vol[0] = '\0';
	float mins[3], maxs[3];
	if (g_eEntity[r] != -1 && GetEntityBounds(g_eEntity[r], mins, maxs))
		Format(vol, sizeof(vol), ", vol centre (%.0f %.0f %.0f) half (%.0f %.0f %.0f)",
			(mins[0] + maxs[0]) * 0.5, (mins[1] + maxs[1]) * 0.5, (mins[2] + maxs[2]) * 0.5,
			(maxs[0] - mins[0]) * 0.5, (maxs[1] - mins[1]) * 0.5, (maxs[2] - mins[2]) * 0.5);

	LogMessage("[layout]   rule %d %s%s%s team %d: %s - entry #%d, ent %d, matched %d, "
		... "shadowed %d, (%.0f %.0f %.0f) -> (%.0f %.0f %.0f), off by %.1f%s%s%s",
		r + 1, g_eClass[r], g_eTarget[r][0] != '\0' ? "/" : "", g_eTarget[r],
		g_eTeam[r], g_verdict[g_eVerdict[r]], g_eOrdinal[r], g_eEntity[r],
		g_eMatches[r], g_eShadowed[r],
		g_eWas[r][0], g_eWas[r][1], g_eWas[r][2],
		g_eIntended[r][0], g_eIntended[r][1], g_eIntended[r][2], g_eOffBy[r],
		g_eRename[r][0] != '\0' ? ", renamed " : "", g_eRename[r], vol);
}

static void ParseConVarVector(ConVar cv, float out[3])
{
	char buf[64];
	cv.GetString(buf, sizeof(buf));
	if (!ParseFloats(buf, out, 3))
		out[0] = out[1] = out[2] = 0.0;
}

// ===========================================================================
// Saying which layout this is, and turning it off
// ===========================================================================

/**
 * Put the console's toggle back after the server config has had its turn.
 *
 * SourceMod calls this once the config for the load has been executed, which
 * is after `OnMapInit` - so this load already read the right value and it is
 * the *next* one this protects. Without it a config line wins permanently and
 * `sm_layout on` is a one-load reprieve.
 */
public void OnConfigsExecuted()
{
	bool want = (g_enabledOverride == 1);
	if (g_enabledOverride >= 0 && g_cvEnabled.BoolValue != want)
	{
		g_reasserting = true;
		g_cvEnabled.SetBool(want);
		g_reasserting = false;
		LogMessage("[layout] a server config set mm_layout_enabled back to %d; "
			... "the console asked for layouts %s and that is what stands - "
			... "sm_layout %s to hand it back to the config",
			want ? 0 : 1, want ? "on" : "off", want ? "off" : "on");
	}
	g_loading = false;
}

/**
 * One line naming the running layout, for chat, for the console and for the
 * log - written once so those three cannot drift apart.
 *
 * A permuted map is the same map: same geometry, same name on the scoreboard,
 * same objectives in the same HUD order. Nothing a player can see says which
 * of sixteen walks round the ladder they have just played, so a complaint
 * about a round arrives with no way to find the round again. This is that way.
 */
static void PresetLine(char[] buf, int maxlen)
{
	char map[PLATFORM_MAX_PATH];
	GetCurrentMap(map, sizeof(map));

	if (g_activeIsStock)
	{
		FormatEx(buf, maxlen, "%s: stock - %s", map, g_activeWhy);
		return;
	}

	// The index is worth the width: "3 of 16" says there are fifteen other
	// rounds to compare against, and "1 of 1" says there are none.
	char of[32];
	of[0] = '\0';
	if (g_presetCount > 0 && g_activeIndex >= 0)
		FormatEx(of, sizeof(of), " (%d of %d)", g_activeIndex + 1, g_presetCount);

	FormatEx(buf, maxlen, "%s: layout '%s'%s - %d edits, %d objective%s%s",
		map, g_activeName, of, g_editsApplied, g_objCount,
		g_objCount == 1 ? "" : "s",
		g_activeUnmatched > 0 ? " - STALE, see below" : "");
}

/** `PresetLine` to one client, or to everyone when `client` is 0. */
static void AnnouncePreset(int client)
{
	char line[192];
	PresetLine(line, sizeof(line));

	if (client > 0)
	{
		if (IsClientInGame(client))
			PrintToChat(client, "[layout] %s", line);
		return;
	}
	PrintToChatAll("[layout] %s", line);

	// The staleness warning is a second line rather than a longer first one:
	// it is the difference between "this round played badly" and "this preset
	// no longer fits this map", and only one of those is worth reporting.
	if (g_activeUnmatched > 0)
		PrintToChatAll("[layout] %d of %d rules matched nothing - this preset "
			... "is stale against this map", g_activeUnmatched, g_eCount);
}

/** Everyone, at the first round start of a map. */
static void AnnounceOnce()
{
	if (g_announced || !g_cvAnnounce.BoolValue)
		return;
	g_announced = true;
	AnnouncePreset(0);
}

public void OnClientPutInServer(int client)
{
	if (!g_cvAnnounce.BoolValue || IsFakeClient(client))
		return;
	// Not inline: a client that has only just been put in the server is still
	// loading and drops chat sent to it. The delay is the same order as the
	// one the objective burst waits out, and for the same reason.
	CreateTimer(10.0, Timer_AnnounceJoin, GetClientUserId(client));
}

public Action Timer_AnnounceJoin(Handle timer, any userid)
{
	int client = GetClientOfUserId(userid);
	if (client > 0)
		AnnouncePreset(client);
	return Plugin_Stop;
}

/**
 * Anyone may ask. It reports and changes nothing.
 *
 * Three lines rather than one, because the question behind it is usually
 * "what do I put in the report": the layout, why it is stock when it is, and
 * what to type to see the rest.
 */
public Action Cmd_Preset(int client, int args)
{
	char line[192];
	PresetLine(line, sizeof(line));
	ReplyToCommand(client, "[layout] %s", line);

	if (!g_cvEnabled.BoolValue)
		ReplyToCommand(client, "[layout] layouts are off - every map loads as "
			... "it ships until mm_layout_enabled is 1 again");
	else if (g_activeUnmatched > 0)
		ReplyToCommand(client, "[layout] %d of %d rules matched nothing: the "
			... "preset was generated against a different version of this map",
			g_activeUnmatched, g_eCount);

	if (g_pinned[0] != '\0')
		ReplyToCommand(client, "[layout] next load: %s (pinned)", g_pinned);
	return Plugin_Handled;
}

/**
 * The toggle only ever means "from the next load".
 *
 * The lump is rewritten in OnMapInit, before a single entity exists, so a map
 * that loaded with a layout has no un-layouted state to go back to - and the
 * half that *is* live, the objective burst that re-teleports caches every
 * round, would if it stopped leave the caches back on their .txt coordinates
 * while every spawn zone stayed permuted. That is worse than either end. So
 * the running map keeps what it loaded with, and this says so where someone
 * flipping the switch will read it.
 */
public void OnEnabledChanged(ConVar convar, const char[] oldValue, const char[] newValue)
{
	if (StrEqual(oldValue, newValue))
		return;

	bool on = convar.BoolValue;
	if (g_reasserting)
		return;                      // OnConfigsExecuted logs its own line
	if (g_loading)
		return;                      // the per-load config; OnConfigsExecuted decides

	// Outside that window this is a person, whether they reached for the cvar
	// or for sm_layout, and either way it is now what the next config exec
	// gets measured against.
	g_enabledOverride = on ? 1 : 0;

	LogMessage("[layout] layouts %s - takes effect on the next load; this map "
		... "keeps the %s it loaded with",
		on ? "on" : "off",
		g_activeIsStock ? "stock map" : "layout");

	if (g_cvAnnounce.BoolValue)
		PrintToChatAll("[layout] layouts %s from the next map load",
			on ? "on" : "off");
}

// ===========================================================================
// Command
// ===========================================================================

public Action Cmd_Layout(int client, int args)
{
	char map[PLATFORM_MAX_PATH];
	GetCurrentMap(map, sizeof(map));

	// The no-argument report is `sm_preset`'s, not a second one written beside
	// it: two reports of the same fact are two things to keep true.
	if (args == 0)
		return Cmd_Preset(client, 0);

	char arg[64];
	GetCmdArg(1, arg, sizeof(arg));

	// `off` and `on` are the toggle, spelled the way someone at the console
	// already talking to this command would reach for it. They set the cvar
	// rather than shadowing it, so there is still exactly one switch and
	// `mm_layout_enabled` in a server config still means what it says.
	if (StrEqual(arg, "off", false) || StrEqual(arg, "on", false))
	{
		bool on = StrEqual(arg, "on", false);
		if (g_cvEnabled.BoolValue == on)
		{
			ReplyToCommand(client, "[layout] layouts are already %s", arg);
			return Plugin_Handled;
		}
		// Recorded before the set, so the re-assertion in OnConfigsExecuted
		// knows what the console asked for even if a config disagrees on the
		// very next load.
		g_enabledOverride = on ? 1 : 0;
		g_cvEnabled.SetBool(on);      // OnEnabledChanged announces, logs, and
		                              // records the same override for itself
		ReplyToCommand(client, "[layout] layouts %s - takes effect on the next "
			... "load; this map keeps what it loaded with, and a server config "
			... "that says otherwise no longer wins", arg);
		return Plugin_Handled;
	}

	if (StrEqual(arg, "list", false))
	{
		KeyValues kv = LoadPresets(map);
		if (kv == null)
		{
			ReplyToCommand(client, "[layout] no presets for %s", map);
			return Plugin_Handled;
		}
		char names[MM_MAX_PRESETS][64];
		int n = ListPresets(kv, names, sizeof(names));
		delete kv;
		ReplyToCommand(client, "[layout] %d preset(s) for %s:", n, map);
		for (int i = 0; i < n; i++)
			ReplyToCommand(client, "  %s%s", names[i],
				StrEqual(names[i], "stock", false)
					? "  (the control - not rotated to; sm_layout stock, or sm_layout off)"
					: (StrEqual(names[i], g_activeName, false) ? "  (active)" : ""));
		return Plugin_Handled;
	}

	strcopy(g_pinned, sizeof(g_pinned), arg);
	ReplyToCommand(client, "[layout] pinned '%s' - takes effect on the next load", arg);
	return Plugin_Handled;
}
