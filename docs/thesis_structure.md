# Thesis Structure

## Title
Optimizing Model Distribution in Containerized Distributed Machine Learning Systems

---

## 1. Introduction
- ML model weights are large, change frequently (split computing), dominant cost in container cold start
- **Gap 1**: existing lazy loading reduces pull latency but adds significant build overhead (eStargz/Nydus require conversion passes on top of already slow sequential BuildKit pipeline)
- **Gap 2**: when weights change, BuildKit re-runs the full sequential layer pipeline — no targeted layer refresh
- **Gap 3**: 2DFS enables faster builds but clients must fully pull all specified allotments — no lazy loading within allotments
- Contributions: (1) lazy loading (stargz) integration into 2dfs builder with low build overhead, (2) layer refresh protocol, (3) prefetch optmization (4) benchmark suite

## 2. Background
- OCI image format, layers, content-addressable storage
- BuildKit architecture and sequential layer pipeline
- containerd, snapshotters, ctr, nerdctl, different components, how client and daemon communicate
- Stargz/eStargz — TOC, lazy pull, build cost, config options
- fuse
- zstandard
- 2DFS — allotments, semantic pull
- Split computing, why fast weight updates, cold start, lazy loading matter in distributed ML
- Model distribution options - containerized, huggingface and other model registries, etc

## 3. Related Work
- Lazy loading: 
    - eStargz - already in the background
    - Nydus
    - Dragonfly
    - SOCI
    - On-demand Container Loading in AWS Lambda
- Parallel image building tools - check 2dfs paper
- layer refresh / model weight update state of the art
- Weights Lazy Loading in ML
    - FaSei: Fast Serverless Edge Inference with Synergistic Lazy Loading and Layer-wise Caching
- Comparison table: build cost / pull latency / update latency (tbd?)

## 4. System Design
- functional requirements
    - Build
        - Builder produces a single image that is both 2dfs-allotment-addressable and stargz-lazy-loadable in one pipeline (no separate conversion pass)
        - Build accepts arguments to tune stargz compression level and chunk size
        - Builder reuses cached layers across gzip and stargz modes without collisions
        - Builder supports zstd compression for layer blobs
    - Distribution
        - Client pulls the image lazily using conventional containerd clients — all layers are included, only TOCs are downloaded eagerly, file contents fetched on demand
        - Allotment tag (`image:tag--r1.c1.r2.c2`) selects which layers receive prefetch annotations (does not restrict which layers are included)
        - Registry still supports non-stargz images and preserves existing tdfs allotment delivery behaviour
        - Registry preserves estargz annotations
    - Update / refresh
        - A single layer (e.g. model weights) can be refreshed in-place without rebuilding upstream layers and stopping the container
        - After refresh, layer contents are available for lazy access
        - Layer refresh provides an option to enable background fetch which loads the new layer contents in the background
- non functional requirements
    - Performance
        - Build overhead of stargz+2dfs vs. plain 2dfs build is bounded by stargz layer conversion overhead
        - Cold-start latency (pull → container ready) lower than full eager pull for partial-access workloads
        - Layer refresh completes in O(1) — TOC fetch only
        - Background fetch after refresh saturates available bandwidth without starving foreground requests
    - Resource efficiency
        - Builder memory usage stays bounded regardless of input image / layer size (no full-layer in-memory buffering)
        - Lazy pull fetches only bytes actually accessed (no over-fetch beyond TOC + prefetch set)
        - Registry storage overhead vs. plain OCI is small (annotations + TOC, not duplicated blobs)
- system architecture
    - component overview + diagram + mark what has been added/updated
    - image format
        - updated image format with stargz annotations
        - image size 
    - 2dfs builder
        - cache hierarchy (`blobs/`, `uncompressed-keys/`, `index/`) with a gzip/stargz discriminator to prevent cross-mode collisions
        - compression options: gzip and zstd
        - streaming layer construction to keep builder memory bounded
    - 2dfs registry
        - under stargz, allotments select the prefetch set rather than restricting which layers are included
        - backward-compatible serving for non-stargz images
    - client-side stack
        - prefetch behavior fix within the snapshotter
        - layer refresh 
            - background fetch
            - TOC replacement, fuse adjustment etc

## 5. Implementation Details
- stargz integration in 2dfs builder: 
    - stargz build integration, size calculation, prefetch landmark removal 
    - build arguments
    - 2std integration
    - memory usage optimization
    - cache handling
- fixing of prefetch behavior in stargz-snapshotter
- 2dfs-registry
    - estargz annotations
    - size optimization
    - config values
    - all layers included
    - prefetch annotations
- Layer refresh: 
    - refresh implementation
    - refresh workflow
    - background fetch after refresh

## 6. Evaluation
- Benchmark suite design and methodology
- Benchmarking hardware. cloudlab
- Build performance
- image update performance
- stargz config value comparison
- build arg comparison. compression level, chunk size
- Layer refresh performance
- Prefetch performance (todo)
- Pull performance: lazy pull, cold vs. warm
- End-to-end: split computing scenario

## 7. Discussion & Conclusions

## 8. Future Work
