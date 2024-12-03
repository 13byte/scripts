#!/bin/bash

# 스크립트 이름 가져오기 (확장자 제외)
SCRIPT_NAME=$(basename "$0" .sh)

# 로그 디렉토리 및 파일 설정
LOG_DIR="./script_logs"
LOG_FILENAME="$(date '+%Y%m%d_%H%M%S')_${SCRIPT_NAME}.log"
LOG_FILE="${LOG_DIR}/${LOG_FILENAME}"
TEMP_DIR="/tmp/replace_temp"

# 처리할 파일 확장자 정의 (공백으로 구분)
# FILE_EXTENSIONS=("ini" "conf" "txt")
FILE_EXTENSIONS=("ini")

# 변경할 텍스트 정의
OLD_TEXT='test'
NEW_TEXT='Tomorrow'

# 로그 디렉토리 생성 함수
setup_logging() {
    # 로그 디렉토리가 없으면 생성
    if [ ! -d "$LOG_DIR" ]; then
        mkdir -p "$LOG_DIR"
        if [ $? -ne 0 ]; then
            echo "Error: Failed to create log directory: $LOG_DIR"
            exit 1
        fi
    fi

    # 로그 파일 생성 및 초기 정보 기록
    touch "$LOG_FILE"
    if [ $? -ne 0 ]; then
        echo "Error: Failed to create log file: $LOG_FILE"
        exit 1
    fi

    echo "Log file created: $LOG_FILE"
    echo "----------------------------------------" >> "$LOG_FILE"
    echo "Script started at: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    echo "Working directory: $(pwd)" >> "$LOG_FILE"
    echo "Target extensions: ${FILE_EXTENSIONS[*]}" >> "$LOG_FILE"
    echo "----------------------------------------" >> "$LOG_FILE"
}

# 로그 함수
log() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] $1" | tee -a "$LOG_FILE"
}

# 로그 정리 함수
cleanup_old_logs() {
    local max_logs=50  # 보관할 최대 로그 파일 수
    local log_count=$(ls -1 "$LOG_DIR"/*_${SCRIPT_NAME}.log 2>/dev/null | wc -l)

    if [ "$log_count" -gt "$max_logs" ]; then
        log "Cleaning up old log files..."
        ls -1t "$LOG_DIR"/${SCRIPT_NAME}_*.log | tail -n +$((max_logs + 1)) | xargs rm -f
        log "Removed $((log_count - max_logs)) old log files"
    fi
}

# 파일 해시 계산 함수
calculate_hash() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        shasum -a 256 "$1" | cut -d' ' -f1
    else
        sha256sum "$1" | cut -d' ' -f1
    fi
}

# 진행률 표시 함수
show_progress() {
    current=$1
    total=$2
    percent=$((current * 100 / total))
    completed=$((percent / 2))
    remaining=$((50 - completed))
    
    printf "\rProgress: [%-${completed}s%-${remaining}s] %d%%" \
        "$(printf '#%.0s' $(seq 1 $completed))" \
        "$(printf ' %.0s' $(seq 1 $remaining))" \
        "$percent"
    echo ""
}

# 파일 미리보기 함수
preview_file() {
    local file="$1"
    local changes=0
    local line_num=0
    
    while IFS= read -r line; do
        ((line_num++))
        if [[ "$line" == *"$OLD_TEXT"* ]]; then
            echo "File: $file"
            echo "  Line $line_num:"
            echo "    Before: $line"
            echo "    After:  ${line//$OLD_TEXT/$NEW_TEXT}"
            ((changes++))
        fi
    done < "$file"
    
    return $changes
}

# 확장자 검사 함수
check_extension() {
    local file="$1"
    local ext="${file##*.}"
    for valid_ext in "${FILE_EXTENSIONS[@]}"; do
        if [[ "$ext" == "$valid_ext" ]]; then
            return 0
        fi
    done
    return 1
}

# 파일 처리 함수
process_file() {
    local file="$1"
    local preview_only="$2"
    local temp_file="$TEMP_DIR/$(basename "$file").tmp"
    local backup_file="${file}.bak"
    
    # 확장자 검사
    check_extension "$file" || return 0
    
    # 미리보기 모드
    if [[ "$preview_only" == "true" ]]; then
        preview_file "$file"
        return $?
    fi
    
    # 원본 해시 계산
    local original_hash=$(calculate_hash "$file")
    
    # 임시 디렉토리 생성
    mkdir -p "$TEMP_DIR"
    
    # 파일 변경
    sed "s|$OLD_TEXT|$NEW_TEXT|g" "$file" > "$temp_file"
    
    # 변경사항이 있는지 확인
    if ! cmp -s "$file" "$temp_file"; then
        # 백업 생성
        cp "$file" "$backup_file"
        
        # 파일 교체
        mv "$temp_file" "$file"
        
        # 새 해시 계산
        local new_hash=$(calculate_hash "$file")
        
        log "File modified: $file"
        log "Original hash: $original_hash"
        log "New hash: $new_hash"
        
        return 1
    fi
    
    rm -f "$temp_file"
    return 0
}

# 메인 함수
main() {
    local preview_only=${1:-true}
    local files=()
    local total_files=0
    local modified_files=0

    setup_logging

    log "Starting directory: $(pwd)"
    log "Processing files with extensions: ${FILE_EXTENSIONS[*]}"
    
    # 지정된 확장자의 파일들 찾기
    local find_pattern=""
    for ext in "${FILE_EXTENSIONS[@]}"; do
        find_pattern="$find_pattern -o -name \"*.$ext\""
    done
    find_pattern="${find_pattern:4}" # 첫 "-o" 제거
    
    while IFS= read -r -d '' file; do
        files+=("$file")
        ((total_files++))
    done < <(eval "find . -type f \( $find_pattern \) -print0")
    
    log "Total files found: $total_files"
    
    if [[ "$preview_only" == "true" ]]; then
        echo "Preview mode - showing potential changes:"
        echo "----------------------------------------"
    fi
    
    # 파일 처리
    local current=0
    for file in "${files[@]}"; do
        ((current++))
        show_progress $current $total_files
        
        if process_file "$file" "$preview_only"; then
            ((modified_files++))
        fi
    done
    echo
    
    # 결과 출력
    if [[ "$preview_only" == "true" ]]; then
        echo "Preview completed. $modified_files files will be modified."
        read -p "Do you want to proceed with the changes? (y/n): " response
        if [[ "$response" =~ ^[Yy]$ ]]; then
            echo "Applying changes..."
            main false
        else
            log "Operation cancelled by user"
            echo "Operation cancelled"
        fi
    else
        log "Operation completed: $modified_files files modified"
        echo "Operation completed: $modified_files files modified"
        echo "Check $LOG_FILE for details"
    fi
    
    # 임시 디렉토리 정리
    rm -rf "$TEMP_DIR"
}

# 스크립트 실행
main true
