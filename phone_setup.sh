#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
#
# This script installs a Debian chroot via proot‑distro, installs
# Ollama and pulls a selected model.  It also sets up helper scripts
# to start and stop the Ollama server, and imports local .gguf models
# from the ./models directory.  Run it from within Termux.

# Update packages and install dependencies
pkg update && pkg upgrade -y
pkg install -y proot-distro git curl tmux

# Install Debian if not already present
if ! proot-distro list | grep -q '^debian$'; then
  proot-distro install debian
fi

# Install Ollama inside Debian
proot-distro login debian -- sh -lc '
  set -e
  apt-get update
  apt-get install -y curl ca-certificates
  curl -fsSL https://ollama.com/install.sh | sh
'

# The model name to pull (defaults to Qwen 1.5B)
MODEL="${1:-qwen2:1_5b}"

# Create helper scripts inside Debian
proot-distro login debian -- sh -lc "
  mkdir -p /root/mobile-llm
  cat >/root/mobile-llm/ollama_env.sh <<'EOS'
export OLLAMA_HOST=0.0.0.0  # expose the server to LAN
export OLLAMA_NUM_PARALLEL=1
EOS
  chmod +x /root/mobile-llm/ollama_env.sh

  cat >/root/mobile-llm/run_ollama.sh <<'EOS'
#!/usr/bin/env bash
set -e
. /root/mobile-llm/ollama_env.sh
tmux has-session -t ollama 2>/dev/null || tmux new-session -d -s ollama 'ollama serve'
sleep 2
ollama list >/dev/null || true
EOS
  chmod +x /root/mobile-llm/run_ollama.sh

  cat >/root/mobile-llm/stop_ollama.sh <<'EOS'
#!/usr/bin/env bash
set -e
tmux has-session -t ollama 2>/dev/null && tmux kill-session -t ollama || true
pkill -f 'ollama serve' 2>/dev/null || true
EOS
  chmod +x /root/mobile-llm/stop_ollama.sh
"

# Import local models if present.  Any .gguf files in ./models are copied
# into the Debian environment so that Ollama can use them without downloading.
REPO_DIR=$(pwd)
LOCAL_MODELS_DIR="${REPO_DIR}/models"

if [ -d "${LOCAL_MODELS_DIR}" ]; then
  for f in "${LOCAL_MODELS_DIR}"/*.gguf; do
    [ -e "$f" ] || continue
    base=$(basename "$f")
    echo "Copying local model $base into Debian..."
    # Create the target directory
    proot-distro login debian -- sh -lc "mkdir -p /root/.ollama/models"
    # Copy the file into the chroot via a temporary file
    proot-distro login debian -- sh -lc "cat > /tmp/.$base.localcopy" < "$f"
    proot-distro login debian -- sh -lc "mv /tmp/.$base.localcopy /root/.ollama/models/$base"
    echo "Copied $base"
  done
fi

# If a .gguf matching the model name exists locally, skip network pull
if [ -f "${LOCAL_MODELS_DIR}/${MODEL}.gguf" ]; then
  echo "Model ${MODEL} found locally.  Skipping download."
else
  echo "Downloading model ${MODEL} via Ollama..."
  proot-distro login debian -- sh -lc "
    . /root/mobile-llm/ollama_env.sh
    ollama pull ${MODEL}
  "
fi

# Start the Ollama server in tmux
proot-distro login debian -- sh -lc "/root/mobile-llm/run_ollama.sh"
echo 'Ollama server started on port 11434.  Use stop_ollama.sh to stop.'
