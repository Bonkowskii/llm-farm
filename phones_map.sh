#!/usr/bin/env bash
set -euo pipefail

BASE_PORT="${1:-11434}"     # możesz podać bazowy port jako 1. arg
MODEL="${2:-tinyllama}"     # domyślna etykieta modelu do phones.json

# Zbierz seriale: kolumna 1, tylko rekordy z "device" w kolumnie 2
mapfile -t SERIALS < <(adb devices -l | awk 'NR>1 && $2=="device"{print $1}')

if [ "${#SERIALS[@]}" -eq 0 ]; then
  echo "Brak urządzeń ADB w stanie 'device'. Podłącz telefony i sprawdź 'adb devices -l'." >&2
  exit 1
fi

echo "Wykryto ${#SERIALS[@]} urządzeń:"
printf ' - %s\n' "${SERIALS[@]}"

# Uporządkuj stare forwardy (opcjonalnie)
# adb forward --remove-all

# Ustaw forwardy i zbuduj JSON
items=()
i=0
for s in "${SERIALS[@]}"; do
  PORT=$((BASE_PORT + i))
  adb -s "$s" forward "tcp:${PORT}" "tcp:11434" >/dev/null
  echo "Mapped serial ${s} -> 127.0.0.1:${PORT}"
  items+=("{ \"host\": \"127.0.0.1\", \"port\": ${PORT}, \"model\": \"${MODEL}\", \"weight\": 1, \"max_concurrency\": 1, \"serial\": \"${s}\" }")
  i=$((i+1))
done

# Zapisz phones.json
{
  echo "["
  for idx in "${!items[@]}"; do
    if [ "$idx" -lt $((${#items[@]}-1)) ]; then
      printf "  %s,\n" "${items[$idx]}"
    else
      printf "  %s\n" "${items[$idx]}"
    fi
  done
  echo "]"
} > phones.json

echo "Zapisano phones.json. Podsumowanie:"
cat phones.json
echo
echo "Aktualne forwardy:"
adb forward --list
