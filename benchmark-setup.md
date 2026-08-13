# Node setup

- cloudlab machine: c6525-25g
- machine [spec](https://www.utah.cloudlab.us/portal/show-nodetype.php?type=c6525-25g&_gl=1*wfe0yn*_ga*MTg1OTgyNjU4MS4xNzcxNDE4OTE3*_ga_6W2Y02FJX6*czE3NzM4NTAzNzgkbzcyJGcwJHQxNzczODUwNDIwJGoxOCRsMCRoMA)

## get registry ip - `10.10.1.2` by default (cloudlab)

```bash
hostname -I | awk '{print $2}'
```

## registry setup

```bash
curl -Lo "${HOME}/registry-node-setup.sh" \
	https://raw.githubusercontent.com/bohdangarchu/lazy-loading-eval/main/node-setup/registry-node-setup.sh

chmod +x "${HOME}/registry-node-setup.sh"
sudo "${HOME}/registry-node-setup.sh"
```

## combined node setup (client + builder)

- install kernel

```bash
cd /mydata
sudo chown $USER /mydata
git clone https://github.com/bohdangarchu/lazy-loading-eval.git
cd lazy-loading-eval/
sudo ./node-setup/install-hwe-kernel.sh
```

- node setup (use correct registry ip)

```bash
cd /mydata/lazy-loading-eval/node-setup
sudo ./combined-node-setup.sh 10.10.1.2 https://github.com/bohdangarchu/stargz-snapshotter.git
```

- env setup
```bash
cd ..
python3 -m venv .venv-benchmark
source .venv-benchmark/bin/activate
pip install -r ./benchmark/requirements.txt
echo "HF_TOKEN=..." > ./benchmark/.env
```

- benchmark uses `shared/config.yaml` by default. Copy desired config

```bash
cp /mydata/lazy-loading-eval/benchmark/shared/config-cloudlab.yaml /mydata/lazy-loading-eval/benchmark/shared/config.yaml 
```

- clone split_llm

```bash
cat > ~/.ssh/id_ed25519 <<'EOF'
-----BEGIN OPENSSH PRIVATE KEY-----
...
-----END OPENSSH PRIVATE KEY-----
EOF

chmod 600 ~/.ssh/id_ed25519
cd /mydata/
git clone git@gitlab.lrz.de:distributed-ml/split-llm-simple.git
```

- install dependencies

```bash
cd /mydata/split-llm-simple
sudo apt install -y graphviz
python3 -m venv .venv-split-llm
source .venv-split-llm/bin/activate
pip install -r requirements.txt
```

- download CV model splits (optional)

```bash
cd /mydata/lazy-loading-eval
./util/download-splits.sh
```

## Run the benchmark

```bash
cd /mydata/lazy-loading-eval
source .venv-benchmark/bin/activate
cd benchmark
./run-bg.sh
```