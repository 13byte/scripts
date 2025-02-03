#!/usr/bin/env python3
import os
import signal
import time
import re

def signal_handler(sig, frame):
   print('\n\n프로그램을 안전하게 종료합니다.')
   exit(0)

def generate_versions():
   v5 = [
       "5.6.17", "5.6.20", "5.6.21", "5.6.22", "5.6.23", "5.6.24", "5.6.25", "5.6.26", 
       "5.6.27", "5.6.28", "5.6.29", "5.6.30", "5.6.31", "5.6.32", "5.6.33", "5.6.34",
       "5.6.35", "5.6.36", "5.6.37", "5.6.38", "5.6.39", "5.6.40", "5.6.41", "5.6.42",
       "5.6.43", "5.6.44", "5.6.45", "5.6.46", "5.6.47", "5.6.48", "5.6.49", "5.6.50",
       "5.6.51"
   ]
   
   v7 = [
       "5.7.4", "5.7.5", "5.7.6", "5.7.7", "5.7.8", "5.7.9", "5.7.10", "5.7.11", 
       "5.7.12", "5.7.13", "5.7.14", "5.7.15", "5.7.16", "5.7.17", "5.7.18", "5.7.19",
       "5.7.20", "5.7.21", "5.7.22", "5.7.23", "5.7.24", "5.7.25", "5.7.26", "5.7.27",
       "5.7.28", "5.7.29", "5.7.30", "5.7.31", "5.7.32", "5.7.33", "5.7.34", "5.7.35",
       "5.7.36", "5.7.37", "5.7.38", "5.7.39", "5.7.40", "5.7.41", "5.7.42", "5.7.43",
       "5.7.44"
   ]
   
   v8 = [
       "8.0.0", "8.0.1", "8.0.2", "8.0.3", "8.0.4", "8.0.11", "8.0.12", "8.0.13", 
       "8.0.14", "8.0.15", "8.0.16", "8.0.17", "8.0.18", "8.0.19", "8.0.20", "8.0.21",
       "8.0.22", "8.0.23", "8.0.24", "8.0.25", "8.0.26", "8.0.27", "8.0.28", "8.0.29",
       "8.0.30", "8.0.31", "8.0.32", "8.0.33", "8.0.34", "8.0.35", "8.0.36", "8.0.37",
       "8.0.38", "8.0.39", "8.0.40", "8.0.41"
   ]
   
   return (v5, v7, v8)

def display_versions():
   try:
       v5, v7, v8 = generate_versions()
       max_len = max(len(v5), len(v7), len(v8))
       
       print("\nMySQL 버전 선택:")
       print("=" * 80)
       fmt = "{:<20}{:<20}{:<20}"
       print(fmt.format("5.6.x", "5.7.x", "8.0.x"))
       print("-" * 80)

       for i in range(0, max_len, 3):
           rows = []
           for j in range(3):
               idx = i + j
               v5_str = f"{idx+1}. {v5[idx]}" if idx < len(v5) else ""
               v7_str = f"{len(v5)+idx+1}. {v7[idx]}" if idx < len(v7) else ""
               v8_str = f"{len(v5)+len(v7)+idx+1}. {v8[idx]}" if idx < len(v8) else ""
               rows.append(fmt.format(v5_str, v7_str, v8_str))
           print("\n".join(rows))

       return v5 + v7 + v8
   except Exception as e:
       print(f"버전 표시 중 오류 발생: {str(e)}")
       raise

def select_version(versions):
   max_attempts = 3
   attempts = 0
   
   while attempts < max_attempts:
       try:
           choice = input("\n버전 번호를 입력하세요 (0: 종료): ").strip()
           if not choice:
               print("입력값이 없습니다.")
               continue
               
           if choice == '0':
               print("프로그램을 종료합니다.")
               exit(0)
               
           choice_idx = int(choice) - 1
           if 0 <= choice_idx < len(versions):
               return versions[choice_idx]
               
           print(f"1-{len(versions)} 사이의 숫자를 입력하세요.")
           
       except ValueError:
           print("올바른 숫자를 입력하세요.")
       except Exception as e:
           print(f"\n오류 발생: {str(e)}")
       
       attempts += 1
       if attempts == max_attempts:
           print("최대 시도 횟수를 초과했습니다.")
           return None

def check_cnf_exists():
   if not os.path.exists('cnf/my.cnf'):
       print("\n/cnf/my.cnf가 없습니다.")
       exit(1)
   return True

def modify_cnf(version):
   try:
       with open('cnf/my.cnf', 'r') as f:
           content = f.read()

       # socket 경로를 버전에 맞게 수정
       socket_path = '/var/run/mysqld/mysqld.sock' if version.startswith('5.6') else '/var/lib/mysql/mysql.sock'
       
       # [mysqld] 섹션의 socket 경로 수정
       content = re.sub(r'(socket\s*=\s*)/[^\n]*', r'\1' + socket_path, content)
       
       # 수정된 내용 저장
       with open('cnf/my.cnf', 'w') as f:
           f.write(content)
           
       return True

   except Exception as e:
       print(f"my.cnf 수정 중 오류 발생: {str(e)}")
       return False

def create_docker_compose(version):
   try:
       container_name = f"mysql_{version.replace('.', '_')}"
       current_dir = os.path.abspath(os.getcwd())
       
       yaml_content = f"""version: '3'
services:
 {container_name}:
   image: mysql:{version}
   container_name: {container_name}
   volumes:
     - {current_dir}/data:/var/lib/mysql
     - {current_dir}/cnf/my.cnf:/etc/my.cnf
   ports:
     - "3306:3306"
   command: --defaults-file=/etc/my.cnf
   environment:
     - MYSQL_ALLOW_EMPTY_PASSWORD=1
"""
       with open("docker-compose.yml", "w") as f:
           f.write(yaml_content)
       return True
       
   except Exception as e:
       print(f"docker-compose.yml 생성 중 오류 발생: {str(e)}")
       return False

def main():
   signal.signal(signal.SIGINT, signal_handler)
   
   try:
       os.makedirs("data", exist_ok=True)
       check_cnf_exists()
       versions = display_versions()
       version = select_version(versions)
       
       if version:
           modify_cnf(version)
           if create_docker_compose(version):
               print("\n설정 완료")
               print("\n실행 방법:")
               print("1. docker compose up -d")
               container_name = f"mysql_{version.replace('.', '_')}"
               print(f"2. docker container exec -it {container_name} bash")
               print("\n문의: jw.song@iunetworks.com")
               print("\n")
           
   except Exception as e:
       print(f"실행 중 오류 발생: {str(e)}")

if __name__ == "__main__":
   main()
