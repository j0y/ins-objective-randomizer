# vbsp / vvis / vrad under Wine.
#
# The tools that actually work here are ficool2's VBSP++/VVIS++/VRAD++, which
# are 64-bit console programs and ship their own compatibility DLLs - see
# tools/fetch-tools.sh. Valve's own SDK tools are 32-bit, so i386 multiarch is
# installed too, in case those get used instead.
#
# Wine is API translation, not emulation: the tools run as native x86-64 code
# and there is no meaningful compile-time penalty.
#
# The prefix lives in cache/wine/prefix on the host (bind-mounted), not in the
# image, so it survives a rebuild and can be deleted to start clean.
FROM debian:bookworm-slim

RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      wine wine32:i386 wine64 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# -all silences the fixme spam that would otherwise bury vbsp's own output.
ENV WINEDEBUG=-all \
    WINEDLLOVERRIDES="mscoree=d;mshtml=d"

# The compile tools are console-only and need no display, so no Xvfb.
ENTRYPOINT ["wine"]
