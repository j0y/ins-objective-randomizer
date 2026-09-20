/**
 * mm_nav.inc — raw CNavArea access for the survey and layout plugins.
 *
 * Ported from insurgencySamrtBots/insurgency-server/scripting/smartbots/navmesh.inc,
 * trimmed to what a layout survey needs (the death-intensity and hiding-spot
 * accessors went with the bot AI they belonged to) and extended with corner
 * reads, connection lengths and whole-mesh enumeration.
 *
 * Why raw memory rather than parsing the shipped .nav offline: see
 * docs/layout-variants.md §4A. The short of it is that the offline parser
 * truncates the connection graph, and five of the workshop maps ship no .nav
 * at all — the engine's own mesh is the only complete source.
 */

#if defined _mm_nav_included
	#endinput
#endif
#define _mm_nav_included

// CUtlVectorUltraConservative<NavConnect>: a single pointer to Data_t.
#define MM_SIZEOF_NAVCONNECT_VEC 4
// NavConnect = { CNavArea *area; float length; }
#define MM_SIZEOF_NAVCONNECT     8
// CUtlVector<T>: { T *m_pMemory; int m_nAlloc; int m_nGrowSize; int m_Size; }
#define MM_UTLVECTOR_SIZE_OFFSET 12
#define MM_UTLVECTOR_MEM_OFFSET  0

#define MM_NAV_INDOOR 0x80

static int g_mmOffCenter   = -1;
static int g_mmOffConnect  = -1;
static int g_mmOffInsFlags = -1;
static int g_mmOffNwCorner = -1;
static int g_mmOffSeCorner = -1;

static Address g_mmPtrTheNavMesh  = Address_Null;
static Address g_mmPtrTheNavAreas = Address_Null;

static Handle g_mmSDK_GetNearestNavArea;
static Handle g_mmSDK_IsPotentiallyVisible;
static Handle g_mmSDK_IsBlocked;

// ===========================================================================
// Field access
// ===========================================================================

stock float MM_LoadFloat(Address base, int offset)
{
	return view_as<float>(LoadFromAddress(base + view_as<Address>(offset), NumberType_Int32));
}

stock void MM_NavArea_GetVector(Address area, int offset, float out[3])
{
	out[0] = MM_LoadFloat(area, offset);
	out[1] = MM_LoadFloat(area, offset + 4);
	out[2] = MM_LoadFloat(area, offset + 8);
}

stock void MM_NavArea_GetCenter(Address area, float center[3])
{
	MM_NavArea_GetVector(area, g_mmOffCenter, center);
}

stock bool MM_NavArea_GetCorners(Address area, float nw[3], float se[3])
{
	if (g_mmOffNwCorner < 0 || g_mmOffSeCorner < 0)
		return false;
	MM_NavArea_GetVector(area, g_mmOffNwCorner, nw);
	MM_NavArea_GetVector(area, g_mmOffSeCorner, se);
	return true;
}

stock bool MM_NavArea_IsIndoor(Address area)
{
	int flags = LoadFromAddress(area + view_as<Address>(g_mmOffInsFlags), NumberType_Int32);
	return (flags & MM_NAV_INDOOR) != 0;
}

stock int MM_NavArea_GetInsFlags(Address area)
{
	return LoadFromAddress(area + view_as<Address>(g_mmOffInsFlags), NumberType_Int32);
}

/** Number of adjacent areas in direction dir (0-3 = N/E/S/W). */
stock int MM_NavArea_GetAdjacentCount(Address area, int dir)
{
	Address vecPtr = view_as<Address>(LoadFromAddress(
		area + view_as<Address>(g_mmOffConnect + dir * MM_SIZEOF_NAVCONNECT_VEC),
		NumberType_Int32));
	if (vecPtr == Address_Null)
		return 0;
	int count = LoadFromAddress(vecPtr, NumberType_Int32);
	if (count < 0 || count > 256)
		return 0;
	return count;
}

stock Address MM_NavArea_GetAdjacentArea(Address area, int dir, int index)
{
	Address vecPtr = view_as<Address>(LoadFromAddress(
		area + view_as<Address>(g_mmOffConnect + dir * MM_SIZEOF_NAVCONNECT_VEC),
		NumberType_Int32));
	if (vecPtr == Address_Null)
		return Address_Null;
	return view_as<Address>(LoadFromAddress(
		vecPtr + view_as<Address>(4 + index * MM_SIZEOF_NAVCONNECT),
		NumberType_Int32));
}

/**
 * The engine's own edge length for a connection. The offline parser has no
 * equivalent — it stores ids only — and these are what make path *distance*
 * (docs/layout-variants.md §4B step 3) mean anything.
 */
stock float MM_NavArea_GetAdjacentLength(Address area, int dir, int index)
{
	Address vecPtr = view_as<Address>(LoadFromAddress(
		area + view_as<Address>(g_mmOffConnect + dir * MM_SIZEOF_NAVCONNECT_VEC),
		NumberType_Int32));
	if (vecPtr == Address_Null)
		return 0.0;
	return MM_LoadFloat(vecPtr, 4 + index * MM_SIZEOF_NAVCONNECT + 4);
}

// ===========================================================================
// Mesh access
// ===========================================================================

stock Address MM_GetTheNavMesh()
{
	if (g_mmPtrTheNavMesh == Address_Null)
		return Address_Null;
	return view_as<Address>(LoadFromAddress(g_mmPtrTheNavMesh, NumberType_Int32));
}

/** Whether whole-mesh enumeration is available (TheNavAreas resolved). */
stock bool MM_HaveAreaVector()
{
	return g_mmPtrTheNavAreas != Address_Null;
}

stock int MM_GetAreaCount()
{
	if (g_mmPtrTheNavAreas == Address_Null)
		return 0;
	int n = LoadFromAddress(
		g_mmPtrTheNavAreas + view_as<Address>(MM_UTLVECTOR_SIZE_OFFSET), NumberType_Int32);
	// A sane mesh is thousands of areas; anything else means the symbol is
	// not what we think it is, and reading on would walk arbitrary memory.
	if (n < 0 || n > 65536)
		return 0;
	return n;
}

stock Address MM_GetArea(int index)
{
	Address mem = view_as<Address>(LoadFromAddress(
		g_mmPtrTheNavAreas + view_as<Address>(MM_UTLVECTOR_MEM_OFFSET), NumberType_Int32));
	if (mem == Address_Null)
		return Address_Null;
	return view_as<Address>(LoadFromAddress(
		mem + view_as<Address>(index * 4), NumberType_Int32));
}

stock Address MM_GetNearestNavArea(float pos[3], bool anyZ = true, float maxDist = 300.0)
{
	Address navMesh = MM_GetTheNavMesh();
	if (navMesh == Address_Null)
		return Address_Null;
	return view_as<Address>(SDKCall(g_mmSDK_GetNearestNavArea, navMesh, pos, anyZ, maxDist, false, false, 0));
}

stock bool MM_NavArea_IsBlocked(Address area, int teamID = 0)
{
	if (g_mmSDK_IsBlocked == null)
		return false;
	return SDKCall(g_mmSDK_IsBlocked, area, teamID, false);
}

stock bool MM_NavArea_IsPotentiallyVisible(Address from, Address to)
{
	if (g_mmSDK_IsPotentiallyVisible == null)
		return false;
	return SDKCall(g_mmSDK_IsPotentiallyVisible, from, to);
}

// ===========================================================================
// Corner-offset probe
// ===========================================================================

/**
 * m_center is known against this build; the corner offsets are not.
 *
 * CNavArea keeps the invariant center.x == (nw.x + se.x) / 2 and likewise for
 * y, with nw strictly north-west of se. That is specific enough to *find* the
 * offsets rather than guess them: scan candidate pairs and keep the one that
 * holds over a sample of real areas.
 *
 * Returns true if the hinted offsets were already right or a working pair was
 * found; the caller should log MM_NavCornerOffsets() so the discovered values
 * can go back into gamedata.
 */
static bool MM_CornersHold(Address samples[16], int n, int nwOff, int seOff)
{
	if (nwOff == seOff)
		return false;
	for (int i = 0; i < n; i++)
	{
		float c[3], nw[3], se[3];
		MM_NavArea_GetVector(samples[i], g_mmOffCenter, c);
		MM_NavArea_GetVector(samples[i], nwOff, nw);
		MM_NavArea_GetVector(samples[i], seOff, se);

		if (nw[0] >= se[0] || nw[1] >= se[1])
			return false;
		// Areas are at most a few thousand units across; a wild read is not.
		if (se[0] - nw[0] > 20000.0 || se[1] - nw[1] > 20000.0)
			return false;
		if (FloatAbs((nw[0] + se[0]) * 0.5 - c[0]) > 0.5)
			return false;
		if (FloatAbs((nw[1] + se[1]) * 0.5 - c[1]) > 0.5)
			return false;
	}
	return true;
}

stock bool MM_NavProbeCorners(Address samples[16], int n)
{
	if (n <= 0)
		return false;

	if (g_mmOffNwCorner >= 0 && g_mmOffSeCorner >= 0
	 && MM_CornersHold(samples, n, g_mmOffNwCorner, g_mmOffSeCorner))
		return true;

	// Corners sit below m_center in the class, and Vectors are 4-aligned.
	for (int nwOff = 4; nwOff < g_mmOffCenter; nwOff += 4)
	{
		for (int seOff = nwOff + 12; seOff <= g_mmOffCenter; seOff += 4)
		{
			if (MM_CornersHold(samples, n, nwOff, seOff))
			{
				g_mmOffNwCorner = nwOff;
				g_mmOffSeCorner = seOff;
				return true;
			}
		}
	}

	g_mmOffNwCorner = -1;
	g_mmOffSeCorner = -1;
	return false;
}

stock void MM_NavCornerOffsets(int &nwOff, int &seOff)
{
	nwOff = g_mmOffNwCorner;
	seOff = g_mmOffSeCorner;
}

// ===========================================================================
// Init
// ===========================================================================

/**
 * Resolve everything out of gamedata/mapmaker.txt.
 *
 * Fatal on the pieces nothing works without; a missing TheNavAreas only costs
 * whole-mesh enumeration, so it is reported and left to the caller.
 */
stock bool MM_NavInit(const char[] tag)
{
	Handle gc = LoadGameConfigFile("mapmaker");
	if (gc == null)
	{
		LogError("[%s] missing gamedata/mapmaker.txt", tag);
		return false;
	}

	g_mmPtrTheNavMesh = GameConfGetAddress(gc, "TheNavMesh");
	if (g_mmPtrTheNavMesh == Address_Null)
	{
		LogError("[%s] could not resolve TheNavMesh", tag);
		delete gc;
		return false;
	}

	g_mmPtrTheNavAreas = GameConfGetAddress(gc, "TheNavAreas");
	if (g_mmPtrTheNavAreas == Address_Null)
		LogMessage("[%s] TheNavAreas did not resolve - falling back to a flood from spawn entities", tag);

	g_mmOffCenter   = GameConfGetOffset(gc, "CNavArea::m_center");
	g_mmOffConnect  = GameConfGetOffset(gc, "CNavArea::m_connect");
	g_mmOffInsFlags = GameConfGetOffset(gc, "CINSNavArea::m_insFlags");
	g_mmOffNwCorner = GameConfGetOffset(gc, "CNavArea::m_nwCorner");
	g_mmOffSeCorner = GameConfGetOffset(gc, "CNavArea::m_seCorner");

	if (g_mmOffCenter < 0 || g_mmOffConnect < 0 || g_mmOffInsFlags < 0)
	{
		LogError("[%s] missing required nav offsets", tag);
		delete gc;
		return false;
	}

	StartPrepSDKCall(SDKCall_Raw);
	if (!PrepSDKCall_SetFromConf(gc, SDKConf_Signature, "CNavMesh::GetNearestNavArea"))
	{
		LogError("[%s] GetNearestNavArea signature not found", tag);
		delete gc;
		return false;
	}
	PrepSDKCall_AddParameter(SDKType_Vector, SDKPass_ByRef);
	PrepSDKCall_AddParameter(SDKType_Bool, SDKPass_Plain);
	PrepSDKCall_AddParameter(SDKType_Float, SDKPass_Plain);
	PrepSDKCall_AddParameter(SDKType_Bool, SDKPass_Plain);
	PrepSDKCall_AddParameter(SDKType_Bool, SDKPass_Plain);
	PrepSDKCall_AddParameter(SDKType_PlainOldData, SDKPass_Plain);
	PrepSDKCall_SetReturnInfo(SDKType_PlainOldData, SDKPass_Plain);
	g_mmSDK_GetNearestNavArea = EndPrepSDKCall();

	StartPrepSDKCall(SDKCall_Raw);
	if (PrepSDKCall_SetFromConf(gc, SDKConf_Signature, "CNavArea::IsPotentiallyVisible"))
	{
		PrepSDKCall_AddParameter(SDKType_PlainOldData, SDKPass_Plain);
		PrepSDKCall_SetReturnInfo(SDKType_Bool, SDKPass_Plain);
		g_mmSDK_IsPotentiallyVisible = EndPrepSDKCall();
	}

	StartPrepSDKCall(SDKCall_Raw);
	if (PrepSDKCall_SetFromConf(gc, SDKConf_Signature, "CNavArea::IsBlocked"))
	{
		PrepSDKCall_AddParameter(SDKType_PlainOldData, SDKPass_Plain);
		PrepSDKCall_AddParameter(SDKType_Bool, SDKPass_Plain);
		PrepSDKCall_SetReturnInfo(SDKType_Bool, SDKPass_Plain);
		g_mmSDK_IsBlocked = EndPrepSDKCall();
	}

	delete gc;

	if (g_mmSDK_GetNearestNavArea == null)
	{
		LogError("[%s] GetNearestNavArea SDKCall prep failed", tag);
		return false;
	}
	return true;
}
