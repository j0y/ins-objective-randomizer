# SourcePawn compiler.
#
# spcomp is a 32-bit binary and needs the SourceMod include tree beside it, so
# it gets its own image rather than being fetched into the repo like the map
# compile tools. The plugins themselves are bind-mounted from /work/plugin and
# the .smx files land in /work/plugin/build.
#
# Version is pinned: a .smx built by a newer compiler will not load on an older
# server, and this is the pairing the upstream smartbots server runs.
FROM debian:bookworm-slim

ARG SM_VERSION=1.11.0-git6968

RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl lib32stdc++6 lib32z1 \
 && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/sourcemod \
 && curl -sqL "https://sm.alliedmods.net/smdrop/1.11/sourcemod-${SM_VERSION}-linux.tar.gz" \
    | tar xz -C /opt/sourcemod

ENV SM_SCRIPTING=/opt/sourcemod/addons/sourcemod/scripting
ENTRYPOINT ["/bin/sh"]
