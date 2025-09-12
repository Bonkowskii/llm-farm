## Na nowe urzadzenie:
adb push ./models/tinyllama.gguf /storage/emulated/0/Download/llm-phone/tinyllama.gguf
adb push ./com.termux_1021.apk /storage/emulated/0/Download/llm-phone/com.termux_1021.apk

TERMUX DAC UPRAWNIENIA W OPCJACH!

cd /storage/emulated/0/Download/llm-phone/ 
printf 'FROM /sdcard/Download/tinyllama.gguf\nTEMPLATE """{{ .Prompt }}"""\n' > Modelfile && \
ollama create tinyllama -f Modelfile && \
ollama list && \
curl -sS http://127.0.0.1:11434/api/tags

ollama serve

## Potem:
./phones_map.sh