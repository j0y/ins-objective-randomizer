# The Insurgency dedicated server, for headless questions about a map:
# does it load, does the checkpoint chain initialise, can nav_generate
# build a .nav for it.
#
# srcds_linux and every _srv.so beside it are 32-bit ELF, so this image
# exists purely to carry i386 libc/libstdc++ that the host does not have.
# The game tree itself is bind-mounted from /work/game, not baked in.
#
# The engine finds libtier0_srv.so / libsteam_api.so by LD_LIBRARY_PATH,
# which srcds_run would normally set; tools/srcds.sh sets it instead.
FROM debian:bookworm-slim

RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      libc6:i386 libstdc++6:i386 lib32gcc-s1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["/bin/sh"]
