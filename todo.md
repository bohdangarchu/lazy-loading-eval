# Ablation study
- run stargz config comparison on cloulab with different values. build a gantt chart and include it in the thesis
    - see https://app.notion.com/p/2dd39f7db6ed80138d90c303a98e3b79?v=2dd39f7db6ed8047b895000cc44c70eb&p=35739f7db6ed80c294d8fa33b14208a8&pm=s for an example
    - it was produced by benchmark/pull_performance/measure_prefetch_pull.py. understand the difference between it vs prefetch layered. 
    - check if it integrtated cleanly with how we run measure.py and run it with our reference models
    - move the script to producing a csv and add the needed code to results repo
- ablation on registry performance with and without stargz+prefetch
- move measure_prefetch_pull to percentage-based x axis