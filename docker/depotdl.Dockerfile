# Steam depot fetcher. Used instead of steamcmd because it can pull a *single*
# depot, and pull Windows depots from Linux - which is how the compile tools
# arrive here without installing the 9 GB Windows client. See tools/fetch-*.sh.
FROM mcr.microsoft.com/dotnet/runtime:8.0-bookworm-slim

ARG DD_VERSION=3.4.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates unzip wget \
 && wget -q "https://github.com/SteamRE/DepotDownloader/releases/download/DepotDownloader_${DD_VERSION}/DepotDownloader-linux-x64.zip" -O /tmp/dd.zip \
 && unzip -q /tmp/dd.zip -d /opt/dd \
 && chmod +x /opt/dd/DepotDownloader \
 && rm /tmp/dd.zip \
 && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["/opt/dd/DepotDownloader"]
