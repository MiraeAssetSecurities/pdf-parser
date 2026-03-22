# 서버 배포 가이드

FastAPI 서버(포트 3000)와 JupyterLab(포트 8000)을 systemd 서비스로 배포하는 방법을 설명합니다.

## 사전 준비

```bash
# 프로젝트 클론 및 의존성 설치
cd /home/ubuntu/pdf-parser
uv sync

# AWS 자격 증명 설정 (Bedrock, S3 접근용)
aws configure
```

## FastAPI 서버 (포트 3000)

### 서비스 파일 설치

```bash
# 서비스 파일 복사
sudo cp service/api.service.example /etc/systemd/system/api.service

# 서비스 등록 및 시작
sudo systemctl daemon-reload
sudo systemctl enable --now api
```

### 서비스 파일 내용 (`service/api.service.example`)

```ini
[Unit]
Description=FastAPI Server for PDF Parser
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/pdf-parser
Environment="PATH=/snap/bin:/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/snap/bin/uv run uvicorn api:app --host 0.0.0.0 --port 3000 --workers 2
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 주요 설정 설명

| 항목 | 값 | 설명 |
|------|---|------|
| `--host 0.0.0.0` | 모든 인터페이스 | 외부 접근 허용 |
| `--port 3000` | 3000번 포트 | API 서버 포트 |
| `--workers 2` | 2개 워커 | CPU 코어 수에 맞춰 조정 |
| `Restart=always` | 항상 재시작 | 비정상 종료 시 10초 후 자동 재시작 |

### API 엔드포인트

서비스 시작 후 다음 엔드포인트를 사용할 수 있습니다:

- **API 문서 (Swagger UI)**: `http://<서버IP>:3000/docs`
- **Health Check**: `http://<서버IP>:3000/health`
- **PDF 처리**: `POST http://<서버IP>:3000/process`
- **OCR 처리**: `POST http://<서버IP>:3000/ocr`
- **Office 처리**: `POST http://<서버IP>:3000/process-office`
- **통합 처리**: `POST http://<서버IP>:3000/process-document`

## JupyterLab (포트 8000)

### 서비스 파일 설치

```bash
# 서비스 파일 복사
sudo cp service/jupyter.service.example /etc/systemd/system/jupyter.service

# 서비스 등록 및 시작
sudo systemctl daemon-reload
sudo systemctl enable --now jupyter
```

### 서비스 파일 내용 (`service/jupyter.service.example`)

```ini
[Unit]
Description=JupyterLab Server for PDF Parser
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/pdf-parser
Environment="PATH=/snap/bin:/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/snap/bin/uv run jupyter lab --ip=0.0.0.0 --port=8000 --no-browser
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### JupyterLab 접속 방법

1. **토큰 확인** (최초 접속 시 필요):
   ```bash
   sudo journalctl -u jupyter -n 50 | grep token
   ```

2. **직접 접속** (서버에 공인 IP가 있는 경우):
   ```
   http://<서버IP>:8000/?token=<토큰값>
   ```

3. **SSH 포트 포워딩** (공인 IP가 없거나 보안이 필요한 경우):
   ```bash
   ssh -L 8000:localhost:8000 ubuntu@<서버IP>
   # 이후 브라우저에서 http://localhost:8000 접속
   ```

## 서비스 관리 명령어

```bash
# ── 상태 확인 ──
sudo systemctl status pdf-parser-api
sudo systemctl status jupyter

# ── 시작 / 중지 / 재시작 ──
sudo systemctl start pdf-parser-api
sudo systemctl stop appdf-parser-apii
sudo systemctl restart pdf-parser-api

# ── 로그 확인 ──
# 최근 로그
sudo journalctl -u pdf-parser-api -n 100
sudo journalctl -u jupyter -n 100

# 실시간 로그 스트리밍
sudo journalctl -u pdf-parser-api -f
sudo journalctl -u jupyter -f

# ── 서비스 비활성화 (부팅 시 자동 시작 해제) ──
sudo systemctl disable pdf-parser-api
sudo systemctl disable jupyter
```

## 서비스 파일 수정 시

서비스 파일을 수정한 경우 반드시 daemon-reload 후 재시작해야 합니다:

```bash
sudo systemctl daemon-reload
sudo systemctl restart pdf-parser-api
sudo systemctl restart jupyter
```

## 보안 참고사항

- JupyterLab은 기본적으로 토큰 인증을 사용합니다. 프로덕션 환경에서는 비밀번호 설정 또는 SSH 포트 포워딩을 권장합니다.
- FastAPI 서버는 인증이 없으므로, 필요 시 보안 그룹(Security Group)이나 리버스 프록시(Nginx)로 접근을 제한하세요.
- 두 서비스 모두 `0.0.0.0`으로 바인딩되어 있어 모든 네트워크 인터페이스에서 접근 가능합니다. 필요에 따라 `127.0.0.1`로 변경하고 리버스 프록시를 사용하세요.
