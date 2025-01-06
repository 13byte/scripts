import subprocess
import re
import csv
import getpass


def is_valid_data(hostname, ip):
    template_pattern = r"\{#[^}]+\}"
    ip_pattern = r"^(\d{1,3}\.){3}\d{1,3}$"
    excluded_prefixes = ("L2", "L3", "fw", "FW")

    # 템플릿 변수 패턴 ({#xxx}) 검사
    if re.search(template_pattern, hostname) or re.search(template_pattern, ip):
        return True, "Not Server"

    # IP 패턴 검사
    if not re.match(ip_pattern, ip):
        return False, None

    # 제외할 prefix 검사
    if len(hostname) >= 2 and hostname[0:2].upper() in [
        x.upper() for x in excluded_prefixes
    ]:
        return True, "Not Server"

    return True, None


def get_os_info(ip):
    try:
        result = subprocess.check_output(
            ["zabbix_get", "-s", ip, "-k", "system.uname"], timeout=10
        )
        os_name = result.decode("utf-8").strip().split()[0]
        return os_name, None
    except subprocess.TimeoutExpired:
        return "Timeout", "OS Check Timeout"
    except Exception as e:
        return "Error", str(e)


def get_tomcat_count(ip):
    try:
        os_info = get_os_info(ip)[0].lower()
        if "windows" in os_info:
            total_count = 0
            tomcat_versions = [
                "tomcat.exe",
                "tomcat6.exe",
                "tomcat7.exe",
                "tomcat8.exe",
                "tomcat9.exe",
                "tomcat10.exe",
                "tomcat11.exe",
            ]

            for version in tomcat_versions:
                try:
                    result = subprocess.check_output(
                        ["zabbix_get", "-s", ip, "-k", f"proc.num[{version}]"],
                        timeout=10,
                    )
                    count = result.decode("utf-8").strip()
                    if count.isdigit():
                        total_count += int(count)
                except:
                    continue
            return str(total_count)
        else:
            result = subprocess.check_output(
                ["zabbix_get", "-s", ip, "-k", "proc.num[java,,,tomcat]"], timeout=10
            )
            count = result.decode("utf-8").strip()
            return count if count.isdigit() else "0"
    except subprocess.TimeoutExpired:
        return "0"
    except Exception as e:
        return "0"


def main():
    try:
        # MySQL 비밀번호 입력
        password = getpass.getpass("Enter MySQL root password: ")

        # 호스트 정보 쿼리
        mysql_cmd = f"""MYSQL_PWD="{password}" mysql -u root zabbix -N -e "
       SELECT DISTINCT 
           h.host AS Hostname,
           i.ip AS IPAddress
       FROM hosts h 
       JOIN interface i ON h.hostid = i.hostid 
       WHERE h.status = 0 AND i.main = 1
       ORDER BY h.host
       " """
        output = subprocess.check_output(mysql_cmd, shell=True).decode("utf-8")

        # 결과 파싱 및 필터링
        rows = [line.split("\t") for line in output.strip().split("\n")]
        valid_results = []
        for row in rows:
            if len(row) >= 2:
                is_valid, note = is_valid_data(row[0], row[1])
                if is_valid:
                    result_row = {
                        "Hostname": row[0],
                        "IPAddress": row[1],
                        "Note": note if note else "",
                    }
                    valid_results.append(result_row)

        # CSV 파일 생성
        output_file = "server_tomcat_status.csv"
        with open(output_file, "w", newline="") as csvfile:
            fieldnames = ["Hostname", "IPAddress", "OS", "TomcatProcessCNT", "Note"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            print("프로세스 정보를 수집하는 중...")
            total_count = len(valid_results)

            for index, row in enumerate(valid_results, 1):
                if not row["Note"]:  # 비고가 없는 경우만 OS와 Tomcat 정보 수집
                    os_info, error = get_os_info(row["IPAddress"])
                    row["OS"] = os_info
                    if error:  # 에러가 있으면 Note에 기록
                        row["Note"] = error
                    row["TomcatProcessCNT"] = get_tomcat_count(row["IPAddress"])
                else:  # 비고가 있는 경우 OS와 Tomcat 정보는 수집하지 않음
                    row["OS"] = ""
                    row["TomcatProcessCNT"] = ""
                writer.writerow(row)
                print(f"처리 중... {index}/{total_count}")

        print(f"CSV 파일이 생성되었습니다: {output_file}")
        print(f"총 {len(valid_results)} 개의 서버가 처리되었습니다.")

    except subprocess.CalledProcessError as e:
        print(f"MySQL 실행 오류: {e}")
    except Exception as e:
        print(f"오류 발생: {e}")


if __name__ == "__main__":
    main()
