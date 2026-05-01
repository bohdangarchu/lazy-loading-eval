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
- Stargz/eStargz — TOC, lazy pull, build cost
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
- non functional requirements
- system architecture

## 4. Implementation Details
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
    - cconfig values
    - all layers included
    - prefetch annotations
- Layer refresh: 
    - refresh implementation
    - refresh workflow
    - background fetch after refresh

## 5. Evaluation
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

## 6. Discussion & Conclusions

## 7. Future Work
