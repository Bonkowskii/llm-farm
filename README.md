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



## Kolejkowanie
for p in "Powiedz cześć z telefonu!" "Capital of Poland?" "Ile to jest 17+25?" "Napisz krótkie haiku o Wiśle"; do
curl -s -X POST http://127.0.0.1:8000/jobs \
-H 'Content-Type: application/json' \
-d "$(jq -n --arg prompt "$p" --arg model "tinyllama" '{prompt:$prompt, model:$model, priority:5}')" \
| jq -r .job_id
done | tee job_ids.txt


## Sprawdzanie statusu
while read id; do
curl -s "http://127.0.0.1:8000/jobs/$id" \
| jq -r '"\(.id)\t\(.status)\t\(.device.serial // "\(.device.host):\(.device.port)")"'
done < job_ids.txt | column -t




## Sprawdzanie wynikow
while read id; do
echo "=== $id ==="
curl -s "http://127.0.0.1:8000/jobs/$id/result" \
| jq -r '.message.content // .response // .text // .output // empty'
done < job_ids.txt
