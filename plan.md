Experiment 1: Allotment Loading Benchmark — Full Reference                                                                                                                                               
                                                        
 What This Experiment Does

 Compares three container image packaging strategies when deploying ML model shards across
 a cluster where each node only needs one shard (allotment):

 ┌─────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │   Variant   │                                                       Description                                                       │
 ├─────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ 2dfs-stargz │ 2DFS build with stargz compression. Client pulls only its allotment column via semantic tag — minimal bytes downloaded. │
 ├─────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ stargz-full │ Standard stargz image containing all allotments. Full image pulled lazily — only accessed chunks fetched on read.       │
 ├─────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ docker-base │ Standard OCI image containing all allotments. Full image downloaded at pull time, no lazy loading.                      │
 └─────────────┴─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

 Goal: measure build time, push time, pull time, image-ready time, allotment-load-in-memory time,
 bytes downloaded during pull and execution, and CPU/RAM on the builder.

 ---
 Infrastructure

 - 1 builder node — builds and pushes images
 - 1 registry node — 2dfs-registry on port 5000
 - 3 client nodes — each pulls and runs with one allotment (hardcoded N=3)

 Baseline setup scripts (already in node-setup/)

 - builder-node-setup.sh — installs: containerd, runc, CNI, nerdctl+buildkitd, Go 1.24, stargz binaries, tdfs (2dfs-builder), Python3
 - client-node-setup.sh — installs: containerd, runc, CNI, nerdctl+buildkitd, Go 1.23.6, stargz-snapshotter (ctr-remote), prometheus, FUSE
 - registry-node-setup.sh — installs Docker, builds and runs 2dfs-registry on port 5000

 Stargz and 2dfs binaries

 Correct versions of containerd-stargz-grpc, ctr-remote, and tdfs are assumed to be
 pre-installed before experiment scripts run. No custom fork of ctr-remote is needed.

 ---
 Known Cache Paths

 2dfs builder cache (on builder)

 ~/.2dfs/blobs/
 ~/.2dfs/uncompressed-keys/
 ~/.2dfs/index/
 Clear: rm -rf ~/.2dfs/blobs/* ~/.2dfs/uncompressed-keys/* ~/.2dfs/index/*

 Stargz + containerd cache (on client)

 STARGZ_ROOT="/var/lib/containerd-stargz-grpc"

 systemctl stop stargz-snapshotter
 rm -rf "${STARGZ_ROOT:?}"/*
 nerdctl image rm -f "${IMAGE}" || true
 ctr content rm $(ctr content ls -q) || true
 systemctl start stargz-snapshotter
 systemctl restart containerd

 ---
 Key Commands Reference

 Build commands (run on builder)

 # 2dfs + stargz
 tdfs build python:3.10-slim $REGISTRY/allotments:2dfs-stargz \
   --enable-stargz -f 2dfs.json --force-http
 tdfs image push $REGISTRY/allotments:2dfs-stargz --force-http

 # stargz full (all allotments, lazy loadable)
 nerdctl build -t allotments:stargz-full -f Dockerfile.allotments .
 nerdctl image convert --estargz --oci allotments:stargz-full \
   $REGISTRY/allotments:stargz-full
 nerdctl push --insecure-registry $REGISTRY/allotments:stargz-full

 # docker base (all allotments, no lazy loading)
 nerdctl build -t $REGISTRY/allotments:docker-base -f Dockerfile.allotments .
 nerdctl push --insecure-registry $REGISTRY/allotments:docker-base

 Pull commands (run on client, IDX = 0|1|2)

 # 2dfs-stargz: semantic tag pulls only column IDX
 # Tag format: --<start_row>.<start_col>.<end_row>.<end_col>
 # Single allotment at column IDX: --0.IDX.0.IDX
 ctr-remote images rpull --plain-http \
   $REGISTRY/allotments:2dfs-stargz--0.$IDX.0.$IDX

 # stargz-full: lazy pull full image (all clients pull same tag)
 ctr-remote images rpull --plain-http $REGISTRY/allotments:stargz-full

 # docker-base: standard pull
 ctr images pull --plain-http $REGISTRY/allotments:docker-base

 Run commands (run on client)

 # stargz variants
 ctr run --rm --snapshotter=stargz $IMAGE experiment1-run-$IDX \
   python3 /workload.py /allotments/<filename>

 # docker-base
 ctr run --rm $IMAGE experiment1-run-$IDX \
   python3 /workload.py /allotments/<filename>

 ---
 Monitoring Architecture

 Builder node

 - node_exporter (process, port 9100) — exposes CPU/RAM metrics
 - prometheus (process, port 9090) — scrapes node_exporter at 127.0.0.1:9100
 - Setup: mirrors exactly what client-node-setup.sh does for prometheus (same version 3.9.1, same systemd unit pattern)

 Client nodes

 - prometheus (already installed by client-node-setup.sh) — scrapes:
   - stargz-snapshotter at 127.0.0.1:8234
   - containerd at 127.0.0.1:9334 path /v1/metrics ← not in default client setup, needs update_client.sh
 - No node_exporter on clients

 Prometheus config on client (/etc/prometheus/prometheus.yml)

 global:
   scrape_interval: 3s
 scrape_configs:
   - job_name: 'stargz-snapshotter'
     static_configs:
       - targets: ['127.0.0.1:8234']
   - job_name: 'containerd'
     static_configs:
       - targets: ['127.0.0.1:9334']
     metrics_path: /v1/metrics

 Prometheus config on builder (/etc/prometheus/prometheus.yml)

 global:
   scrape_interval: 3s
 scrape_configs:
   - job_name: 'node'
     static_configs:
       - targets: ['127.0.0.1:9100']

 ---
 How to Extract Metrics (no web UI)

 Timing — parse stdout logs

 Every script prints ISO8601 UTC timestamps to stdout. Redirect to log file and parse:
 ./builder/build.sh $REGISTRY 2dfs-stargz 2>&1 | tee build_2dfs.log
 grep "\[20" build_2dfs.log   # all timestamp lines

 CPU / RAM during build — prometheus range query API

 After build finishes, query builder prometheus with the start/end from the log:
 START="2026-02-27T10:00:00Z"
 END="2026-02-27T10:05:00Z"

 # CPU %: average non-idle across all cores, 5s resolution
 curl -sG "http://localhost:9090/api/v1/query_range" \
   --data-urlencode "query=100 - (avg(rate(node_cpu_seconds_total{mode='idle'}[30s])) * 100)" \
   --data-urlencode "start=$START" --data-urlencode "end=$END" \
   --data-urlencode "step=5s" > cpu.json

 # RAM used in bytes
 curl -sG "http://localhost:9090/api/v1/query_range" \
   --data-urlencode "query=node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes" \
   --data-urlencode "start=$START" --data-urlencode "end=$END" \
   --data-urlencode "step=5s" > ram.json
 Output: JSON with .data.result[].values = array of [unix_timestamp, value_string].

 Bytes downloaded — raw metrics snapshot + diff

 Before and after each phase, snapshot the stargz metrics endpoint to a file:
 curl -s http://localhost:8234/metrics > stargz_before_pull.txt
 ./client/pull.sh $REGISTRY 2dfs-stargz 0
 curl -s http://localhost:8234/metrics > stargz_after_pull.txt
 ./client/run.sh 2dfs-stargz 0
 curl -s http://localhost:8234/metrics > stargz_after_run.txt
 Find byte-related counters: grep -i byte stargz_before_pull.txt
 Delta between snapshots = bytes fetched in that phase.
 This is more precise than querying prometheus (no interpolation, exact counter values).

 ---
 Experiment Folder: experiments/experiment1/

 experiments/experiment1/
 ├── builder/
 │   ├── prepare.py               # Download model, generate 2dfs.json
 │   ├── build.sh                 # Build + push variant (args: REGISTRY VARIANT)
 │   ├── Dockerfile.allotments    # Used by stargz-full and docker-base
 │   └── clear_cache.sh           # Clears 2dfs builder cache
 ├── client/
 │   ├── pull.sh                  # Pull image (args: REGISTRY VARIANT ALLOTMENT_IDX)
 │   ├── workload.py              # Read allotment file into memory + timestamps
 │   ├── run.sh                   # Run container (args: REGISTRY VARIANT ALLOTMENT_IDX)
 │   └── clear_cache.sh           # Clears stargz + containerd caches (arg: IMAGE_REF)
 └── monitoring/
     ├── setup_builder.sh         # Install node_exporter + prometheus on builder
     ├── update_client.sh         # Overwrite client prometheus.yml (add containerd job)
     ├── query_builder_metrics.sh # Extract CPU/RAM from builder prometheus (args: START END)
     └── snapshot_client_metrics.sh # Snapshot stargz/containerd metrics (arg: LABEL)

 Script: builder/prepare.py

 Reuses model-experiment/prepare.py logic:
 - snapshot_download() from huggingface_hub
 - Args: --n 3 --outdir ./allotments
 - Selects N largest downloaded files
 - Writes to ./allotments/, generates 2dfs.json (row=0, col=0..N-1, src/dst under /allotments/)
 - Requires: pip3 install huggingface_hub on builder (not in builder-node-setup.sh currently)
 - Note: needs a model with N large files. DistilBERT has only 1 (~267MB). Use a multi-shard model.

 Script: builder/build.sh

 Timestamps printed to stdout, VARIANT drives which build path runs (see Key Commands above).
 BASE_PULL timestamps wrap the base image pull (before nerdctl/tdfs build starts).

 Script: builder/Dockerfile.allotments

 FROM python:3.10-slim
 COPY allotments/ /allotments/
 COPY workload.py /workload.py

 Script: builder/clear_cache.sh

 rm -rf ~/.2dfs/blobs/* ~/.2dfs/uncompressed-keys/* ~/.2dfs/index/*

 Script: client/workload.py

 Mirrors model-experiment/main.py exactly:
 import sys, time
 path = sys.argv[1]
 before = time.time()
 with open(path, 'rb') as f:
     data = f.read()
 after = time.time()
 fmt = "%Y-%m-%dT%H:%M:%S"
 print(f"FILE_READ_START: {time.strftime(fmt, time.gmtime(before))}Z")
 print(f"FILE_READ_END:   {time.strftime(fmt, time.gmtime(after))}Z")
 print(f"duration: {after - before:.6f}s  bytes: {len(data)}")
 With stargz lazy loading, f.read() triggers the actual network fetch — this captures
 allotment-loaded-in-memory time including any lazy download.

 Script: client/run.sh

 Prints RUN_START before and RUN_END after the ctr run call. Container prints its own
 FILE_READ_START / FILE_READ_END. Derives image name and file path from VARIANT + IDX.

 Script: monitoring/setup_builder.sh

 Mirrors the prometheus installation in client-node-setup.sh (lines 196–228):
 1. Download node_exporter latest → /usr/local/bin/node_exporter
 2. Create /etc/systemd/system/node_exporter.service (ExecStart on port 9100, Restart=always)
 3. Download prometheus 3.9.1 → /usr/local/bin/prometheus
 4. Write /etc/prometheus/prometheus.yml scraping 127.0.0.1:9100
 5. Create /etc/systemd/system/prometheus.service (same pattern as client setup)
 6. systemctl daemon-reload && systemctl enable --now node_exporter prometheus

 Script: monitoring/update_client.sh

 Overwrites /etc/prometheus/prometheus.yml with both stargz + containerd jobs, then
 systemctl restart prometheus.

 Script: monitoring/query_builder_metrics.sh

 - Args: START END (ISO8601)
 - Runs the two curl queries above (CPU + RAM), saves to cpu_${START}.json and ram_${START}.json

 Script: monitoring/snapshot_client_metrics.sh

 - Args: LABEL
 - Snapshots http://localhost:8234/metrics → stargz_${LABEL}.txt
 - Snapshots http://localhost:9334/v1/metrics → containerd_${LABEL}.txt

 ---
 Run Order Summary

 On builder (once per experiment):
 pip3 install huggingface_hub   # if not already done
 python3 builder/prepare.py --n 3 --outdir ./allotments
 monitoring/setup_builder.sh    # installs node_exporter + prometheus

 # For each variant:
 builder/clear_cache.sh
 builder/build.sh $REGISTRY 2dfs-stargz   # or stargz-full or docker-base

 On each client (IDX = 0, 1, 2):
 monitoring/update_client.sh    # once, updates prometheus config

 # For each variant:
 client/clear_cache.sh $IMAGE_REF
 monitoring/snapshot_client_metrics.sh before_pull
 client/pull.sh $REGISTRY $VARIANT $IDX 2>&1 | tee pull_${VARIANT}_${IDX}.log
 monitoring/snapshot_client_metrics.sh after_pull
 client/run.sh $REGISTRY $VARIANT $IDX 2>&1 | tee run_${VARIANT}_${IDX}.log
 monitoring/snapshot_client_metrics.sh after_run

 On builder (after build is done):
 # Extract timestamps from build log, then:
 monitoring/query_builder_metrics.sh $BUILD_START $PUSH_END

 ---
 Missing Dependencies / Node Setup Fixes Needed

 ┌───────────────────────────────────────────┬─────────┬───────────────────────────────────────────────────────────────────────────────────────────┐
 │                   Issue                   │  Node   │                                            Fix                                            │
 ├───────────────────────────────────────────┼─────────┼───────────────────────────────────────────────────────────────────────────────────────────┤
 │ pip3 + huggingface_hub not installed      │ builder │ Add to builder-node-setup.sh: apt-get install python3-pip && pip3 install huggingface_hub │
 ├───────────────────────────────────────────┼─────────┼───────────────────────────────────────────────────────────────────────────────────────────┤
 │ node_exporter + prometheus not on builder │ builder │ Run monitoring/setup_builder.sh                                                           │
 ├───────────────────────────────────────────┼─────────┼───────────────────────────────────────────────────────────────────────────────────────────┤
 │ Client prometheus missing containerd job  │ client  │ Run monitoring/update_client.sh                                                           │
 └───────────────────────────────────────────┴─────────┴───────────────────────────────────────────────────────────────────────────────────────────┘