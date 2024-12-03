#!/bin/zsh

echo "@는 각 요소를 하나의 완전한 문자열로 처리"
echo "*는 공백을 기준으로 분리되어 처리\n"
FILE_EXTENSIONS=("hello world.ini" "test file.txt" ".c")

echo "[*] 결과: $FILE_EXTENSIONS[*]"
echo "-------------------"
echo "[@] 결과: $FILE_EXTENSIONS[@]\n"

echo "[@] 결과: \"${FILE_EXTENSIONS[@]}\""
echo "-----------------------------------"
echo "[*] 결과: \"${FILE_EXTENSIONS[*]}\"\n"

IFS=","

echo "IFS 적용 후 결과\n"
echo "[*] 결과: $FILE_EXTENSIONS[*]"
echo "-------------------"
echo "[@] 결과: $FILE_EXTENSIONS[@]\n"

IFS=" "

echo "[@] 순회:"
for file in "${FILE_EXTENSIONS[@]}"; do
    echo "항목: $file"
done
echo "-----------------------------------"
echo "[*] 순회:"
for file in "${FILE_EXTENSIONS[*]}"; do
    echo "항목: $file"
done
