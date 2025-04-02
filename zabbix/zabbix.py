#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pymysql
import pandas as pd
import sys
import datetime
import os
import time

# 데이터베이스 연결 정보
DB_HOST = "117.52.98.103"  # Zabbix DB 서버 주소
DB_USER = "zabbix"  # Zabbix DB 사용자
DB_PASSWORD = "K@3#Ut8^Pm(y"  # Zabbix DB 비밀번호
DB_NAME = "zabbix"  # Zabbix DB 이름
DB_PORT = 3306  # MySQL 기본 포트

# 조회 기간 설정 (2025년 3월 1일 00:00:00 부터 2025년 3월 31일 23:50:59 까지)
START_DATE = "2025-03-01 00:00:00"
END_DATE = "2025-03-31 23:50:59"

# Unix timestamp로 변환
START_TIME = int(
    datetime.datetime.strptime(START_DATE, "%Y-%m-%d %H:%M:%S").timestamp()
)
END_TIME = int(datetime.datetime.strptime(END_DATE, "%Y-%m-%d %H:%M:%S").timestamp())

# IP당 처리 대기 시간 (초)
DELAY_PER_IP = 1

# 디버그 모드
DEBUG_MODE = True


def db_connect():
    """데이터베이스 연결 생성"""
    try:
        connection = pymysql.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            port=DB_PORT,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        return connection
    except pymysql.MySQLError as e:
        print(f"데이터베이스 연결 오류: {e}")
        sys.exit(1)


def get_host_by_ip(connection, ip):
    """IP 주소로 호스트 정보 조회"""
    try:
        with connection.cursor() as cursor:
            query = """
            SELECT h.hostid, h.host, h.name
            FROM hosts h
            JOIN interface i ON h.hostid = i.hostid
            WHERE i.ip = %s
            AND h.status = 0  -- 활성화된 호스트만
            LIMIT 1
            """

            cursor.execute(query, (ip,))
            host = cursor.fetchone()

            if not host:
                print(f"주의: IP {ip}에 대한 활성화된 호스트를 찾을 수 없습니다.")

        return host
    except pymysql.MySQLError as e:
        print(f"호스트 조회 오류: {e}")
        return None


def detect_os_type(connection, hostid):
    """호스트의 운영체제 유형 감지 (Linux 또는 Windows)"""
    try:
        with connection.cursor() as cursor:
            # 먼저 운영체제 관련 아이템 찾기
            query = """
            SELECT key_
            FROM items
            WHERE hostid = %s
            AND (
                key_ LIKE 'system.uname' OR 
                key_ LIKE 'system.sw.os%%' OR
                key_ LIKE '%%windows%%' OR
                key_ LIKE '%%linux%%'
            )
            AND status = 0
            LIMIT 10
            """

            cursor.execute(query, (hostid,))
            os_items = cursor.fetchall()

            # 아이템 이름으로 판단
            for item in os_items:
                key = item["key_"].lower()
                if "windows" in key:
                    return "Windows"
                if "linux" in key:
                    return "Linux"

            # 디스크 아이템 패턴으로 판단
            query = """
            SELECT key_
            FROM items
            WHERE hostid = %s
            AND (
                key_ LIKE 'vfs.fs.size%%' OR
                key_ LIKE '%%disk%%'
            )
            AND status = 0
            LIMIT 10
            """

            cursor.execute(query, (hostid,))
            disk_items = cursor.fetchall()

            for item in disk_items:
                key = item["key_"].lower()
                if "[c:" in key or "[d:" in key:
                    return "Windows"
                if "[/" in key:
                    return "Linux"

            # 호스트 이름으로 추측
            query = """
            SELECT host, name
            FROM hosts
            WHERE hostid = %s
            """

            cursor.execute(query, (hostid,))
            host_info = cursor.fetchone()

            if host_info:
                hostname = host_info["host"].lower() + " " + host_info["name"].lower()
                if "win" in hostname or "windows" in hostname:
                    return "Windows"
                if (
                    "lin" in hostname
                    or "linux" in hostname
                    or "unix" in hostname
                    or "cent" in hostname
                    or "ubuntu" in hostname
                ):
                    return "Linux"

            # 기본값
            return "Unknown"

    except pymysql.MySQLError as e:
        print(f"OS 타입 감지 오류: {e}")
        return "Unknown"


def get_cpu_utilization_item(connection, hostid):
    """CPU utilization 관련 아이템 조회"""
    try:
        with connection.cursor() as cursor:
            # CPU utilization 관련 아이템 찾기
            query = """
            SELECT 
                i.itemid, 
                i.name, 
                i.key_, 
                i.value_type
            FROM 
                items i
            WHERE 
                i.hostid = %s
                AND i.status = 0
                AND (
                    (i.key_ LIKE '%%cpu.util%%' OR i.key_ LIKE '%%cpu%%util%%') OR
                    (i.name LIKE '%%CPU utilization%%' OR i.name LIKE '%%CPU usage%%')
                )
            ORDER BY 
                CASE 
                    WHEN i.key_ = 'system.cpu.util' THEN 1
                    WHEN i.key_ LIKE 'system.cpu.util%%' THEN 2
                    WHEN i.key_ LIKE '%%cpu.util%%' THEN 3
                    ELSE 4
                END
            LIMIT 1
            """

            cursor.execute(query, (hostid,))
            item = cursor.fetchone()

            if DEBUG_MODE and item:
                print(
                    f"  선택된 CPU utilization 아이템: {item['name']} (키: {item['key_']})"
                )
            elif DEBUG_MODE:
                print("  CPU utilization 아이템을 찾을 수 없습니다.")

            return item
    except pymysql.MySQLError as e:
        print(f"CPU 아이템 조회 오류: {e}")
        return None


def get_memory_utilization_item(connection, hostid):
    """메모리 utilization 관련 아이템 조회"""
    try:
        with connection.cursor() as cursor:
            # 메모리 utilization 관련 아이템 찾기
            query = """
            SELECT 
                i.itemid, 
                i.name, 
                i.key_, 
                i.value_type
            FROM 
                items i
            WHERE 
                i.hostid = %s
                AND i.status = 0
                AND (
                    i.key_ = 'vm.memory.util' OR
                    i.key_ LIKE '%%memory%%util%%' OR
                    i.key_ LIKE '%%mem%%util%%' OR
                    i.name LIKE '%%Memory utilization%%' OR
                    i.name LIKE '%%Memory usage%%'
                )
            ORDER BY 
                CASE 
                    WHEN i.key_ = 'vm.memory.util' THEN 1
                    WHEN i.key_ LIKE '%%memory.util%%' THEN 2
                    ELSE 3
                END
            LIMIT 1
            """

            cursor.execute(query, (hostid,))
            item = cursor.fetchone()

            if DEBUG_MODE and item:
                print(
                    f"  선택된 메모리 utilization 아이템: {item['name']} (키: {item['key_']})"
                )
            elif DEBUG_MODE:
                print("  메모리 utilization 아이템을 찾을 수 없습니다.")

            return item
    except pymysql.MySQLError as e:
        print(f"메모리 아이템 조회 오류: {e}")
        return None


def get_disk_usage_item(connection, hostid, os_type):
    """디스크 사용량 아이템 조회 (OS 타입에 따라 다름)"""
    try:
        with connection.cursor() as cursor:
            if os_type == "Linux":
                # 리눅스 - 루트(/) 파티션
                query = """
                SELECT 
                    i.itemid, 
                    i.name, 
                    i.key_, 
                    i.value_type
                FROM 
                    items i
                WHERE 
                    i.hostid = %s
                    AND i.status = 0
                    AND (
                        i.key_ LIKE 'vfs.fs.size[/,pused]' OR
                        i.key_ LIKE 'vfs.fs.size[/,used]%%' OR
                        (i.key_ LIKE 'vfs.fs.size%%' AND i.name LIKE '%%/ %%used%%')
                    )
                ORDER BY 
                    CASE 
                        WHEN i.key_ = 'vfs.fs.size[/,pused]' THEN 1
                        WHEN i.key_ LIKE 'vfs.fs.size[/,%%]' THEN 2
                        ELSE 3
                    END
                LIMIT 1
                """
            elif os_type == "Windows":
                # 윈도우 - D: 드라이브
                query = """
                SELECT 
                    i.itemid, 
                    i.name, 
                    i.key_, 
                    i.value_type
                FROM 
                    items i
                WHERE 
                    i.hostid = %s
                    AND i.status = 0
                    AND (
                        i.key_ LIKE 'vfs.fs.size[D:,pused]' OR
                        i.key_ LIKE 'vfs.fs.size[D:,used]%%' OR
                        i.key_ LIKE 'vfs.fs.size[D%%,pused]' OR
                        (i.key_ LIKE 'vfs.fs.size%%' AND i.name LIKE '%%D:%%used%%')
                    )
                ORDER BY 
                    CASE 
                        WHEN i.key_ = 'vfs.fs.size[D:,pused]' THEN 1
                        WHEN i.key_ LIKE 'vfs.fs.size[D:,%%]' THEN 2
                        WHEN i.key_ LIKE 'vfs.fs.size[D%%]' THEN 3
                        ELSE 4
                    END
                LIMIT 1
                """
            else:
                # OS 타입을 알 수 없는 경우
                query = """
                SELECT 
                    i.itemid, 
                    i.name, 
                    i.key_, 
                    i.value_type
                FROM 
                    items i
                WHERE 
                    i.hostid = %s
                    AND i.status = 0
                    AND (
                        i.key_ LIKE 'vfs.fs.size%%pused%%' OR
                        (i.key_ LIKE 'vfs.fs.size%%' AND i.name LIKE '%%used%%')
                    )
                LIMIT 1
                """

            cursor.execute(query, (hostid,))
            item = cursor.fetchone()

            if DEBUG_MODE and item:
                print(
                    f"  선택된 디스크 사용량 아이템({os_type}): {item['name']} (키: {item['key_']})"
                )
            elif DEBUG_MODE:
                print(f"  디스크 사용량 아이템({os_type})을 찾을 수 없습니다.")

            return item
    except pymysql.MySQLError as e:
        print(f"디스크 아이템 조회 오류: {e}")
        return None


def get_item_data(connection, itemid, value_type):
    """아이템 데이터 조회 (trends와 history 테이블 모두 시도)"""
    # 1. trends 테이블에서 조회
    try:
        with connection.cursor() as cursor:
            # 값 타입에 따라 적절한 테이블 선택
            trends_table = (
                "trends"
                if value_type == 0
                else "trends_uint" if value_type == 3 else None
            )

            if trends_table:
                # 테이블이 존재하는지 확인
                check_table_query = "SHOW TABLES LIKE %s"
                cursor.execute(check_table_query, (trends_table,))

                if cursor.fetchone():
                    query = f"""
                    SELECT 
                        AVG(value_avg) as avg_value,
                        MAX(value_max) as max_value,
                        COUNT(*) as record_count
                    FROM {trends_table}
                    WHERE itemid = %s
                    AND clock BETWEEN %s AND %s
                    """

                    cursor.execute(query, (itemid, START_TIME, END_TIME))
                    result = cursor.fetchone()

                    if (
                        result
                        and result["avg_value"] is not None
                        and result["record_count"] > 0
                    ):
                        return {
                            "avg": round(result["avg_value"], 1),
                            "max": round(result["max_value"], 1),
                            "count": result["record_count"],
                            "source": f"{trends_table}",
                        }
    except pymysql.MySQLError as e:
        print(f"  trends 데이터 조회 오류: {e}")

    # 2. history 테이블에서 조회
    try:
        with connection.cursor() as cursor:
            history_table = (
                "history"
                if value_type == 0
                else "history_uint" if value_type == 3 else None
            )

            if history_table:
                check_table_query = "SHOW TABLES LIKE %s"
                cursor.execute(check_table_query, (history_table,))

                if cursor.fetchone():
                    query = f"""
                    SELECT 
                        AVG(value) as avg_value,
                        MAX(value) as max_value,
                        COUNT(*) as record_count
                    FROM {history_table}
                    WHERE itemid = %s
                    AND clock BETWEEN %s AND %s
                    """

                    cursor.execute(query, (itemid, START_TIME, END_TIME))
                    result = cursor.fetchone()

                    if (
                        result
                        and result["avg_value"] is not None
                        and result["record_count"] > 0
                    ):
                        return {
                            "avg": round(result["avg_value"], 1),
                            "max": round(result["max_value"], 1),
                            "count": result["record_count"],
                            "source": f"{history_table}",
                        }
    except pymysql.MySQLError as e:
        print(f"  history 데이터 조회 오류: {e}")

    # 데이터가 없거나 오류가 발생한 경우
    return None


def process_host(connection, ip):
    """특정 호스트 처리 및 데이터 수집"""
    # 호스트 정보 조회
    host = get_host_by_ip(connection, ip)

    if not host:
        return None

    hostid = host["hostid"]
    hostname = host["name"]

    print(f"  호스트 '{hostname}' (ID: {hostid}) 데이터 조회 중...")

    # OS 타입 감지
    os_type = detect_os_type(connection, hostid)
    print(f"  감지된 OS 타입: {os_type}")

    # 결과 저장용 딕셔너리
    result = {
        "hostname": hostname,
        "host": host["host"],
        "ip": ip,
        "os_type": os_type,
        "cpu_avg": None,
        "cpu_max": None,
        "memory_avg": None,
        "memory_max": None,
        "disk_avg": None,
        "disk_max": None,
        "status": "정상",
        "cpu_item": None,
        "memory_item": None,
        "disk_item": None,
    }

    # 1. CPU utilization 아이템 조회 및 데이터 수집
    cpu_item = get_cpu_utilization_item(connection, hostid)

    if cpu_item:
        result["cpu_item"] = cpu_item["key_"]
        cpu_data = get_item_data(connection, cpu_item["itemid"], cpu_item["value_type"])

        if cpu_data:
            result["cpu_avg"] = cpu_data["avg"]
            result["cpu_max"] = cpu_data["max"]
            print(
                f"  CPU 데이터({cpu_data['source']}): 평균={result['cpu_avg']}%, 최대={result['cpu_max']}%, 레코드 수={cpu_data['count']}"
            )
        else:
            print(f"  CPU 데이터를 찾을 수 없습니다.")
            result["status"] = "CPU 데이터 없음"
    else:
        print(f"  CPU utilization 아이템을 찾을 수 없습니다.")
        result["status"] = "CPU 아이템 없음"

    # 2. 메모리 utilization 아이템 조회 및 데이터 수집
    memory_item = get_memory_utilization_item(connection, hostid)

    if memory_item:
        result["memory_item"] = memory_item["key_"]
        memory_data = get_item_data(
            connection, memory_item["itemid"], memory_item["value_type"]
        )

        if memory_data:
            result["memory_avg"] = memory_data["avg"]
            result["memory_max"] = memory_data["max"]
            print(
                f"  메모리 데이터({memory_data['source']}): 평균={result['memory_avg']}%, 최대={result['memory_max']}%, 레코드 수={memory_data['count']}"
            )
        else:
            print(f"  메모리 데이터를 찾을 수 없습니다.")
            result["status"] = (
                "메모리 데이터 없음"
                if result["status"] == "정상"
                else result["status"] + ", 메모리 데이터 없음"
            )
    else:
        print(f"  메모리 utilization 아이템을 찾을 수 없습니다.")
        result["status"] = (
            "메모리 아이템 없음"
            if result["status"] == "정상"
            else result["status"] + ", 메모리 아이템 없음"
        )

    # 3. 디스크 사용량 아이템 조회 및 데이터 수집
    disk_item = get_disk_usage_item(connection, hostid, os_type)

    if disk_item:
        result["disk_item"] = disk_item["key_"]
        disk_data = get_item_data(
            connection, disk_item["itemid"], disk_item["value_type"]
        )

        if disk_data:
            result["disk_avg"] = disk_data["avg"]
            result["disk_max"] = disk_data["max"]
            print(
                f"  디스크 데이터({disk_data['source']}): 평균={result['disk_avg']}%, 최대={result['disk_max']}%, 레코드 수={disk_data['count']}"
            )
        else:
            print(f"  디스크 데이터를 찾을 수 없습니다.")
            result["status"] = (
                "디스크 데이터 없음"
                if result["status"] == "정상"
                else result["status"] + ", 디스크 데이터 없음"
            )
    else:
        print(f"  디스크 사용량 아이템을 찾을 수 없습니다.")
        result["status"] = (
            "디스크 아이템 없음"
            if result["status"] == "정상"
            else result["status"] + ", 디스크 아이템 없음"
        )

    return result


def main():
    # IP 목록 파일 경로
    ip_file = "server_ips.txt"

    # 파일이 존재하는지 확인
    if not os.path.exists(ip_file):
        print(f"오류: {ip_file} 파일이 존재하지 않습니다.")
        sys.exit(1)

    # IP 목록 읽기
    with open(ip_file, "r") as f:
        ip_list = [line.strip() for line in f if line.strip()]

    if not ip_list:
        print("오류: IP 목록이 비어 있습니다.")
        sys.exit(1)

    # IP 수 출력
    ip_count = len(ip_list)
    print(f"총 {ip_count}개의 IP 주소를 처리합니다.")
    estimated_time = ip_count * DELAY_PER_IP
    print(f"예상 처리 시간: 약 {estimated_time}초 ({estimated_time/60:.1f}분)")

    # 데이터베이스 연결
    connection = db_connect()

    try:
        # 결과 저장용 리스트
        results = []

        # 각 IP에 대해 데이터 수집
        for i, ip in enumerate(ip_list, 1):
            print(f"\n[{i}/{ip_count}] IP {ip} 처리 중...")

            # 호스트 처리
            result = process_host(connection, ip)

            if result:
                results.append(result)
                print(
                    f"  호스트 '{result['hostname']}' 데이터 처리 완료 (상태: {result['status']})"
                )

            # 마지막 IP가 아니면 대기
            if i < ip_count:
                print(f"  다음 IP 처리 전 {DELAY_PER_IP}초 대기 중...")
                time.sleep(DELAY_PER_IP)

        if results:
            # 결과를 데이터프레임으로 변환
            df = pd.DataFrame(results)

            # 열 순서 조정
            columns = [
                "hostname",
                "host",
                "ip",
                "os_type",
                "cpu_avg",
                "cpu_max",
                "memory_avg",
                "memory_max",
                "disk_avg",
                "disk_max",
                "status",
                "cpu_item",
                "memory_item",
                "disk_item",
            ]
            df = df[columns]

            def format_percentage(value):
                if pd.isna(value):
                    return value

                if value < 1:
                    # 1 미만은 소수점 한 자리 유지
                    return f"{value:.1f}%"
                else:
                    # 1 이상은 정수로 변환
                    return f"{int(round(value, 0))}%"

            # 숫자 열에 포맷 적용
            for column in [
                "cpu_avg",
                "cpu_max",
                "memory_avg",
                "memory_max",
                "disk_avg",
                "disk_max",
            ]:
                df[column] = df[column].apply(format_percentage)

            # Excel 파일로 저장
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            excel_file = f"zabbix_server_stats_{timestamp}.xlsx"
            df.to_excel(excel_file, index=False)

            print(f"\n데이터가 '{excel_file}' 파일로 저장되었습니다.")
            print(f"총 {len(results)}개 호스트의 데이터가 처리되었습니다.")

            # 문제가 있는 서버 요약
            problem_servers = [r for r in results if r["status"] != "정상"]
            if problem_servers:
                print("\n주의! 다음 서버에서 문제가 발생했습니다:")
                for server in problem_servers:
                    print(
                        f"  - 호스트: {server['hostname']}, IP: {server['ip']}, 문제: {server['status']}"
                    )
            else:
                print("\n모든 서버의 데이터가 성공적으로 처리되었습니다.")
        else:
            print("\n처리된 데이터가 없습니다.")

    finally:
        # 데이터베이스 연결 종료
        connection.close()


if __name__ == "__main__":
    main()
