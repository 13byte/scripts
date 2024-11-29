#!/bin/bash

# 삭제할 파일 목록 먼저 표시
echo "삭제될 파일 목록:"
find . -type f \( -name "*.bak" -o -name "*.bak.bak" \)

# 사용자 확인
read -p "위 파일들을 삭제하시겠습니까? (y/n): " confirm

if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
    # 파일 삭제 실행
    find . -type f \( -name "*.bak" -o -name "*.bak.bak" \) -delete
    echo "백업 파일 삭제가 완료되었습니다."
else
    echo "작업이 취소되었습니다."
fi
