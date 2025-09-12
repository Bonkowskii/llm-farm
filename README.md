# Distributed Inference on Mobile Devices

This repository contains a **ready‑to‑use** framework for running a small
language model locally on multiple Android devices and forwarding
requests to them from a central server.  It was designed with two
requirements in mind:

* **Fully offline operation** – once the model has been pulled on each
  phone the system does not call any external APIs or services.
* **Simple data parallelism** – each phone holds a complete copy of
  the model.  Requests are load‑balanced across the fleet; there is no
  cross‑device communication or model sharding.  This makes the
  architecture robust and easy to maintain while delivering good
  throughput on multiple handsets【728248464826799†L173-L215】.

The project builds on
[`proot‑distro`](https://github.com/termux/proot-distro) to create an
isolated Debian environment inside [Termux](https://play.google.com/store/apps/details?id=com.termux),
and uses [Ollama](https://ollama.com) to serve quantised models.  The
central server is written in Python using FastAPI and dispatches
requests in a round‑robin manner.  You can run this gateway on any
Linux host (including Windows WSL) that has access to the phones
through Wi‑Fi.

## Why replicate instead of split?

Splitting a model across several devices requires complex tensor or
pipeline parallelism protocols and fast interconnects.  Research
projects such as LinguaLinked and MDI‑LLM explore these techniques,
but they involve careful layer placement, activation exchange and
synchronisation – far beyond what typical phones can handle.  In
contrast, replicating the entire model on each device avoids
communication overhead and reduces latency, at the cost of higher
aggregate memory usage.  Smartphones with 6–8 GB of RAM can handle
3 billion parameter models, while 12 GB devices can run 7–8 billion
parameter models【728248464826799†L260-L268】.  For older or mid‑range
phones consider quantised 3 B models (e.g. `phi-2` or `qwen2.5-1.5B`).

## Directory structure

```
mobile_inference/
├── README.md            # this file
├── phones.json          # list of phones with IPs and optional weights
├── server.py            # FastAPI gateway
├── phone_setup.sh       # install Debian + Ollama on each phone
└── models/
    └── README.md        # instructions for pre‑downloading models
```

### server.py

The gateway reads `phones.json` and exposes three main endpoints:

* `POST /ask` – send a prompt to one of the phones.  The request
  body should include at least the `prompt` field and may specify
  `model`, `system` or other generation options.  Results are
  cached using an LRU cache by default.
* `POST /ask_stream` – same as `/ask` but streams back tokens as they
  are produced.
* `POST /ask_batch` – send multiple prompts in parallel; useful when
  you want to utilise all devices at once.

Each phone entry can specify a `weight` and `max_concurrency`.  A
weight greater than 1 will cause the gateway to select that phone
more often during round‑robin scheduling.  `max_concurrency` limits
the number of simultaneous requests per phone (useful if some
handsets are slower than others).  If a phone fails repeatedly, a
circuit‑breaker pauses it for 30 seconds before retrying.

### phone_setup.sh

Run this script on **each phone** via Termux.  It installs Debian
inside proot, fetches Ollama and optionally pulls a model.  The first
argument of the script is the model name, for example:

```bash
bash phone_setup.sh qwen2:1_5b
```

By default the script exposes the Ollama server on port 11434 to the
local network.  You should ensure that your Wi‑Fi router allows
connections between devices.  When the model download finishes, the
script starts a `tmux` session running `ollama serve`.  You can stop
the server later via `/root/mobile-llm/stop_ollama.sh` from within
the Debian environment.

### models/

Ollama stores downloaded models in its internal cache.  This directory
is provided for convenience if you prefer to distribute models
manually (e.g. using a USB cable instead of downloading on each
phone).  Place the downloaded `gguf` or `safetensors` files here and
copy them to the same location inside the Debian environment on your
phone (`/root/.ollama/models/`).  Consult Ollama’s documentation for
details.

## Quick start

1. **Prepare each phone**
   * Install Termux from Google Play or F‑Droid.
   * Run `pkg update && pkg upgrade -y`.
   * Clone this repository or download `phone_setup.sh` and run
     `bash phone_setup.sh <model>` to install Debian and Ollama and
     pull the model.
   * Note the phone’s IP address on your Wi‑Fi network (Settings →
     About phone → Status or run `ip a` in Termux).

2. **Edit `phones.json`**
   * Add an entry for each phone: `{ "host": "192.168.1.50",
     "port": 11434, "weight": 1, "max_concurrency": 1 }`.

3. **Run the gateway**
   * On your laptop or PC, create a virtual environment and install
     dependencies:

     ```bash
     python -m venv .venv
     source .venv/bin/activate
     pip install -r requirements.txt
     ```

   * Start the server:

     ```bash
     uvicorn server:app --host 0.0.0.0 --port 8000
     ```

   * To test, send a request via curl:

     ```bash
     curl -X POST http://localhost:8000/ask \
          -H "Content-Type: application/json" \
          -d '{"prompt":"Jakie są zalety modeli działających offline?"}'
     ```

4. **Use the API**
   * `/health` returns the health of each phone (whether the
     `/api/tags` endpoint responds, the number of inflight requests,
     etc.).
   * `/metrics` exposes Prometheus counters (total requests,
     failures and average latency per phone).

## Improvements over the prototype

The original prototype distributed prompts to phones in a simple
round‑robin manner.  This release includes several enhancements:

* **Weighted scheduling and concurrency limits** – each phone entry
  may include a `weight` and `max_concurrency` so that more powerful
  devices receive more traffic and slower ones are not overloaded.
* **Circuit‑breaker** – if a phone fails three health checks in a row
  the gateway waits 30 seconds before sending more requests to it.
* **LRU caching** – repeated prompts (with the same `system` and
  `options`) return instantly without hitting the phones.  The cache
  can be disabled by setting `ENABLE_LRU_CACHE = False` in
  `server.py`.
* **Streaming** – `/ask_stream` returns results as soon as tokens are
  available.  This is particularly useful on phones where generating
  a full answer may take several seconds.
* **Batch requests** – you can submit a list of prompts in a single
  call; the gateway will dispatch them concurrently across the fleet.

## Notes on model selection

Choosing an appropriate model is critical on mobile.  On modern
Snapdragon 8‑series devices a 7–8 billion parameter model can output
around 11 tokens per second【728248464826799†L173-L215】.  However,
performance drops rapidly on older phones: the article
<https://www.androidauthority.com/install-deepseek-android-3521203/> reports
that devices with only 6 GB of RAM struggle with 7 B models and
instead run 3 B models at around 5 tokens per second【728248464826799†L260-L268】.
If you plan to run multiple devices, start with a smaller model such
as `phi-2` or `qwen2.5-1.5b-instruct`.  Avoid 14 B models unless your
phone has at least 16 GB of RAM【728248464826799†L260-L268】.

Although vLLM provides sophisticated tensor and pipeline parallelism
for distributed inference across GPUs【328583610654069†L1519-L1524】, these
techniques require high‑bandwidth interconnects (e.g. NVLink) and are
not feasible on consumer smartphones.  Instead, this project opts for
simple data parallelism by replicating the model on each phone.

## Future work

* **Model sharding** – research projects such as vLLM’s expert
  parallelism support splitting models across devices and could, in
  theory, be adapted to heterogeneous clusters.  However, they
  require low‑latency interconnects and an efficient collective
  communications library (e.g. NCCL or P2P channels)【328583610654069†L1519-L1524】.
* **GPU/NPU acceleration** – at the time of writing (September 2025)
  Ollama runs models entirely on the CPU on Android; neither the
  Neural Processing Unit nor GPU is utilised.  Should native
  acceleration become available, updating `phone_setup.sh` to enable
  it would deliver major speedups【728248464826799†L90-L103】.
* **Retrieval‑augmented generation** – the Polish RAG pipeline from
  Bonkowski’s ChatBot project demonstrates how to combine local
  documents with a small LLM【90652671942711†L183-L200】.  Integrating a
  similar mechanism could enable the phones to answer questions using
  custom corpora stored locally on the central server.

## phones.json
host ip address:
ip -4 addr show wlan0 | awk '/inet / {print $2}' | cut -d/ -f1

Quick start

Prepare each phone

Install Termux from Google Play or F‑Droid.

Run pkg update && pkg upgrade -y.

Clone this repository or download phone_setup.sh and run
bash phone_setup.sh <model> to install Debian and Ollama and
pull the model.

Note the phone’s IP address on your Wi‑Fi network (Settings →
About phone → Status or run ip a in Termux).

ADB alternative: If you connect the phone via USB and forward
its port 11434 to your computer using adb forward tcp:11434 tcp:11434,
you can skip the IP step. The phone’s Ollama server becomes
available on 127.0.0.1:11434 on your laptop. See
the USB / ADB Mode

section for a detailed walkthrough.

Edit phones.json

Add an entry for each phone: { "host": "192.168.1.50", "port": 11434, "weight": 1, "max_concurrency": 1 }.

Run the gateway

On your laptop or PC, create a virtual environment and install
dependencies:

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt


Start the server:

uvicorn server:app --host 0.0.0.0 --port 8000


To test, send a request via curl:

curl -X POST http://localhost:8000/ask \
-H "Content-Type: application/json" \
-d '{"prompt":"Jakie są zalety modeli działających offline?"}'


Use the API

/health returns the health of each phone (whether the
/api/tags endpoint responds, the number of inflight requests,
etc.).

/metrics exposes Prometheus counters (total requests,
failures and average latency per phone).

Improvements over the prototype

The original prototype distributed prompts to phones in a simple
round‑robin manner. This release includes several enhancements:

Weighted scheduling and concurrency limits – each phone entry
may include a weight and max_concurrency so that more powerful
devices receive more traffic and slower ones are not overloaded.

Circuit‑breaker – if a phone fails three health checks in a row
the gateway waits 30 seconds before sending more requests to it.

LRU caching – repeated prompts (with the same system and
options) return instantly without hitting the phones. The cache
can be disabled by setting ENABLE_LRU_CACHE = False in
server.py.

Streaming – /ask_stream returns results as soon as tokens are
available. This is particularly useful on phones where generating
a full answer may take several seconds.

Batch requests – you can submit a list of prompts in a single
call; the gateway will dispatch them concurrently across the fleet.

Notes on model selection

Choosing an appropriate model is critical on mobile. On modern
Snapdragon 8‑series devices a 7–8 billion parameter model can output
around 11 tokens per second
androidauthority.com
. However,
performance drops rapidly on older phones: the article
https://www.androidauthority.com/install-deepseek-android-3521203/
reports
that devices with only 6 GB of RAM struggle with 7 B models and
instead run 3 B models at around 5 tokens per second
androidauthority.com
.
If you plan to run multiple devices, start with a smaller model such
as phi-2 or qwen2.5-1.5b-instruct. Avoid 14 B models unless your
phone has at least 16 GB of RAM
androidauthority.com
.

Although vLLM provides sophisticated tensor and pipeline parallelism
for distributed inference across GPUs
docs.vllm.ai
, these
techniques require high‑bandwidth interconnects (e.g. NVLink) and are
not feasible on consumer smartphones. Instead, this project opts for
simple data parallelism by replicating the model on each phone.

USB / ADB Mode (recommended for testing and multi‑phone labs)

Wi‑Fi connectivity is not always reliable, and in some environments
routers block traffic between devices or prevent services in proot
from binding to 0.0.0.0. A simple workaround is to connect the
phone to your computer via USB and use ADB port forwarding to access
its Ollama server. This works whether you run Ollama inside Debian
with proot‑distro or directly in Termux. The only requirement is
that curl http://127.0.0.1:11434/api/tags produces a JSON response
on the phone.

1. Prepare the phone

Ensure the model is installed and the server is running on the phone.
Check locally:

# inside Debian
proot-distro login debian -- sh -lc 'ollama list && curl -s http://127.0.0.1:11434/api/tags'

# or directly in Termux
ollama list && curl -s http://127.0.0.1:11434/api/tags


Both commands should return a JSON list of tags/models. If not,
start the server (ollama serve) and try again. To warm up the
model and reduce latency on the first request, you can run:

curl -s http://127.0.0.1:11434/api/generate \
-H "Content-Type: application/json" \
-d '{"model":"tinyllama","prompt":"","stream":false,"keep_alive":"1h","options":{"num_predict":1}}'

2. Forward a port on the computer

Connect the phone to your computer over USB and run:

adb forward tcp:11434 tcp:11434


This maps 127.0.0.1:11434 on your laptop to 127.0.0.1:11434 on the phone. You can test the
connection from your laptop:

curl http://127.0.0.1:11434/api/tags


If you see the same JSON response as on the phone, the tunnel is working.

For multiple phones, forward each device to a different local port (e.g.
11434, 11435, 11436) using the -s SERIAL option:

# Device serials from `adb devices`
adb -s SERIAL_A forward tcp:11434 tcp:11434
adb -s SERIAL_B forward tcp:11435 tcp:11434


Then populate phones.json with entries like:

[
{ "host": "127.0.0.1", "port": 11434, "model": "tinyllama", "weight": 1 },
{ "host": "127.0.0.1", "port": 11435, "model": "tinyllama", "weight": 1 }

## License

This code is provided for educational purposes.  Check the
licensing terms of any models you run (e.g. Qwen, Phi or LLaMa) to
ensure compliance with their use‑case and redistribution policies【90652671942711†L183-L200】.