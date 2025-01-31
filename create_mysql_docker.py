#!/usr/bin/env python3
import os
import signal
import time

def signal_handler(sig, frame):
   print('\n\n프로그램을 안전하게 종료합니다.')
   exit(0)

def generate_versions():
   return (
       [f"5.6.{x}" for x in range(52)],
       [f"5.7.{x}" for x in range(45)],
       [f"8.0.{x}" for x in range(42)]
   )

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
      - {current_dir}/mysql_data:/var/lib/mysql
    ports:
      - "3306:3306"
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
       os.makedirs("mysql_data", exist_ok=True)
       versions = display_versions()
       version = select_version(versions)
       
       if version and create_docker_compose(version):
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
