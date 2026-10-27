# 고정 HTTPS 주소와 열쇠 없는 Google 받아쓰기

회사 이름은 **Voiney**, 제품은 **Voiney Lab**이다. 이 안내는 사람이 콘솔과
EC2에서 적용하는 순서다. 저장소 변경만으로 DNS·클라우드 권한·서비스가 바뀌지는 않는다.
운영 데이터와 실행 중인 서비스의 소유자를 먼저 확인한다. 설정값·키·토큰·초대 링크는
화면 공유·작업 보고·메신저에 붙이지 않는다.

예시는 `lab.example.com`, `/opt/voineylab`, 서버 사용자 `voineylab`이다.
실제 설치가 `/home/ubuntu/...`, 사용자 `ubuntu`이면 **모든** 경로·사용자와
서비스 파일을 그 값으로 바꾼다. `~/vl-lane-ops` 같은 작업 줄에서 운영 서비스를
띄우지 않는다. 기존 실행 방식의 데이터 경로를 확인하기 전 `run_pilot.sh`로
바꾸면 기존 기록이 다른 폴더에 가려질 수 있다. 데이터 이전은 이 안내의 자동 작업이 아니다.

| 사람이 할 일 | 계획 시간(측정값 아님) | 비용을 결정하는 것 |
|---|---|---|
| 도메인 후보·갱신료 확인, 구매 | 15–30분 | 보통 도메인용 예산 연 2–5만 원부터; 프리미엄 이름 제외 |
| Cloudflare 연결·DNS 전파 | 조작 20–40분, 전파는 더 걸릴 수 있음 | Free 요금제와 기존 EC2 사용; 구매 전 최신 조건 확인 |
| 서버·이름 있는 터널 서비스 등록 | 30–60분 | EC2 기존 비용, 도메인 외 추가 자원은 만들지 않음 |
| AWS 역할·Google WIF 연결 | 60–120분 | IAM 구성과 별도로 기존 Speech API 사용량 과금 |
| 백업 수동 복구 연습, 일일 실행 여부 결정 | 30–60분 | 저장 용량·암호화 저장소·외부 보관 비용 |

위 시간·예산은 작업 계획용 추정이다. 도메인 재고·상표·등록/갱신 가격과
무료 서비스 조건을 결제 화면에서 확인한 뒤 비용 상한을 정한다.

## 1. 도메인 고르기

`voineylab.com`은 제품 이름을 그대로 쓰기 쉽고, `voiney.co.kr`은 회사 주소로
쓰고 `lab.voiney.co.kr`을 붙이기 쉽다. 두 이름의 구매 가능 여부는 확인 안 됨이다.
등록·갱신·이전 비용은 다르며 첫해 할인보다 **다음 해 갱신료(VAT 포함)**로 비교한다.

| 등록처·후보 | 비교할 가격대·결제 단위 | 사업자 정보 확인 | 근거·사람이 확인할 화면 |
|---|---|---|---|
| 가비아, `voiney.co.kr` 또는 `.com` | 원화 연간 예산 2–5만 원부터 비교; 현재 판매가는 확인 안 됨 | `.co.kr` 자격·국내 주소·사업자등록증 요구 여부를 등록처에 확인. 사업자 명의와 개인 명의를 구분 | [가비아 도메인](https://domain.gabia.com/), [KRNIC 도메인 등록 안내](https://krnic.or.kr/) |
| 후이즈, 같은 후보 | 원화 갱신가·부가세·이전료를 가비아와 비교; 현재 판매가는 확인 안 됨 | 국내 도메인 등록 자격·세금계산서 요건 확인 | [후이즈 도메인](https://domain.whois.co.kr/) |
| Cloudflare Registrar, `.com` 후보 | 달러 결제·환율·세금 포함 연간 견적 확인; 현재 판매가 확인 안 됨 | `.com`은 사업자용 이름과 등록 자격이 별개. 등록자 연락처 필요. `.co.kr` 지원은 확인 안 됨 | [Registrar](https://www.cloudflare.com/products/registrar/), [지원 TLD](https://www.cloudflare.com/tld-policies/) |

가격 확인일은 **사람이 실제 가격표·결제 화면을 연 날**을 구매 기록에 적는다.
이 문서의 링크·가격·콘솔 표기는 외부 문서 열람 제한 아래 작성했으며 최신 화면에서
대조하지 못했다. 확인하지 않은 가격에 확인 날짜를 붙이지 않는다.
등록 자격은 [KRNIC](https://krnic.or.kr/)과 등록처 약관으로 확정한다.
회사 명의 연락처, 자동 갱신, 결제 담당자, 2단계 인증, 계정 복구 수단도 함께 정한다.

## 2. Cloudflare Free에 도메인 연결

아래는 영문 UI 기준 탐색 이름이며 현재 콘솔에서의 표기는 확인 안 됨이다.
화면이 다르면 끝의 공식 안내에서 현재 위치를 확인한다.

1. 등록처에서 고른 도메인을 구매한다.
2. Cloudflare 대시보드에서 계정을 만들고 이메일 확인·2단계 인증을 마친다.
3. **Websites → Add a domain**에서 도메인 자체(예: `example.com`)를 입력한다.
4. **Select a plan → Free → Continue**를 고른다.
5. **Review your DNS records**에서 기존 A·CNAME·MX·TXT를 원래 DNS와 대조한다. 이메일 레코드를 지우지 않는다.
6. Cloudflare가 보여 주는 두 **Nameservers**를 별도 작업 메모에 적는다.
7. 등록처의 **네임서버 설정/변경**에서 그 두 값으로 바꾸고 저장한다.
8. Cloudflare의 도메인 상태가 **Active**가 될 때까지 기다린다.
9. **SSL/TLS → Edge Certificates**에서 해당 호스트의 인증서 발급 상태를 확인한다.
10. 기존 DNSSEC가 있으면 등록처의 옛 DS와 새 Cloudflare DNSSEC 순서를 공식 안내대로 처리한다. 옛 DS를 남기면 DNS 전체가 실패할 수 있다.

등록처는 그대로 두고 네임서버만 Cloudflare에 맡길 수 있다.
[도메인 추가](https://developers.cloudflare.com/fundamentals/manage-domains/add-site/),
[네임서버 연결](https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/),
[DNSSEC 이전](https://developers.cloudflare.com/dns/dnssec/).

## 3. EC2 서버를 부팅 때 자동으로 시작

기존 서버 프로세스·서비스·데이터 위치는 사람이 확인한다. 같은 포트에서 두 서버를
띄우지 않는다. 기존 자동 배포가 다른 서비스를 다시 시작한다면 먼저 그 서비스 이름을
맞춘다. `scripts/run_pilot.sh`는 로그인 계정·파일럿 데이터 위치를 고정한다
([운영 안내](DEPLOYMENT_RUNBOOK.md) 2절).

아래 명령은 이미 `/opt/voineylab`에 설치와 `.venv`, 비공개 `.env`가 준비돼 있고,
서비스 사용자에게 해당 설치·데이터 경로의 권한이 있을 때 실행한다.
저장소 복사·운영 데이터 이동·소유권 재귀 변경은 하지 않는다.

```bash
cd /opt/voineylab
sudo install -m 644 deploy/voineylab.service /etc/systemd/system/voineylab.service
sudo systemctl daemon-reload
sudo systemctl enable --now voineylab.service
sudo systemctl is-active voineylab.service
curl --fail --max-time 10 http://127.0.0.1:8080/healthz
curl --fail --max-time 10 http://127.0.0.1:8080/readyz
```

사용자·설치 경로가 다르면 설치 **전** `deploy/voineylab.service`의 복사본에서
`User`, `Group`, `WorkingDirectory`, `ExecStart`를 바꾼다. 전용 사용자가 없는
새 설치에서만 `sudo useradd --system --create-home --home-dir /var/lib/voineylab --shell /usr/sbin/nologin voineylab`으로 만든다.
그 경우 Google 자격 경로도 `/var/lib/voineylab/.config/voiney/`로 바꾼다.
기존 키의 내용을 출력하거나 다른 사용자에게 읽게 하지 않는다.

서비스는 127.0.0.1:8080에서만 듣는다. AWS 보안 그룹에 8080을 열지 않는다.
EC2 재부팅 시험은 실험이 없는 시간에 사람이 하고 위 두 점검을 다시 한다.

## 4. 이름 있는 터널과 고정 주소

현재 임시 `trycloudflare.com` 주소는 재시작하면 바뀐다. 이름 있는 터널은
등록한 DNS 이름을 같은 터널 UUID에 붙인다. EC2 공인 IP가 바뀌어도 DNS를
EC2 IP로 바꿀 필요가 없다. Cloudflare가 외부 HTTPS를 받고 같은 EC2의
cloudflared가 로컬 HTTP로 보낸다. 이 구성에서는 Caddy가 앞에 필요하지 않다.
기존 `scripts/pilot_https/Caddyfile.example`은 공인 서버에 직접 HTTPS로 들어오는
별도 구성이다. 같은 이름을 두 구성으로 동시에 전환하지 않는다.

### 4.1 cloudflared 설치(사람 실행, Ubuntu)

먼저 [공식 패키지 안내](https://pkg.cloudflare.com/index.html)의 현재 Ubuntu
설치 명령과 서명 키를 대조한다. 이미 설치됐으면 `command -v cloudflared`와
`cloudflared --version`으로 확인하고 경로를 서비스의 `/usr/bin/cloudflared`와 맞춘다.

```bash
sudo install -d -m 755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
sudo apt-get update
sudo apt-get install cloudflared
cloudflared --version
```

### 4.2 터널 만들기(EC2에서, sudo 없이)

```bash
cloudflared tunnel login
cloudflared tunnel create voiney-lab
```

`login`이 안내하는 브라우저 주소를 본인 브라우저에서 열고 연결할 도메인을 선택해
**Authorize**한다. 생성 결과의 UUID만 아래 `TUNNEL_ID`에 넣는다.
`~/.cloudflared/cert.pem`은 계정 관리 자격이며 터널 JSON도 비밀이다. 둘 다 저장소
밖에 두고 출력·공유하지 않는다. 서비스에는 JSON 하나만 필요하다.

```bash
TUNNEL_ID='본인-터널-UUID'
LAB_HOST='lab.example.com'
cloudflared tunnel route dns "$TUNNEL_ID" "$LAB_HOST"
```

같은 호스트에 기존 A·AAAA·CNAME이 있으면 자동 덮어쓰기를 하지 말고,
Cloudflare **DNS → Records**에서 충돌 레코드를 확인한 뒤 전환한다.
터널 DNS는 UUID의 `.cfargotunnel.com`을 가리킨다.

### 4.3 설정·systemd 등록

새 전용 사용자가 없을 때만 다음 `useradd`를 실행한다.

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin cloudflared
sudo install -d -o root -g cloudflared -m 750 /etc/cloudflared
sudo install -o cloudflared -g cloudflared -m 600 "$HOME/.cloudflared/$TUNNEL_ID.json" "/etc/cloudflared/$TUNNEL_ID.json"
sudo install -o root -g cloudflared -m 640 deploy/cloudflared.yml.example /etc/cloudflared/config.yml
sudoedit /etc/cloudflared/config.yml
```

에디터에서 `TUNNEL_UUID` 두 곳을 실제 UUID로, `lab.YOUR_DOMAIN`을 실제 호스트로
바꾼다. `service: http://127.0.0.1:8080`과 마지막 `http_status:404`는 둔다.

```bash
sudo -u cloudflared cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
sudo install -m 644 deploy/cloudflared.service /etc/systemd/system/cloudflared.service
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared.service
sudo systemctl is-active cloudflared.service
curl --fail --max-time 15 "https://$LAB_HOST/healthz"
curl --fail --max-time 15 "https://$LAB_HOST/readyz"
```

서비스 상태와 로그는 서버에서만 본다. 로그 전체나 브라우저 인증 링크를 보고서에
붙이지 않는다. 재부팅 뒤에도 두 서비스가 `active`이고 HTTPS 점검이 성공해야 한다.
임시 터널 종료는 해당 프로세스의 소유자·실행 위치를 확인한 사람이 한다.
연결 실패 시 [공식 로컬 터널 안내](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/local-management/create-local-tunnel/)와
[Linux 서비스 안내](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/local-management/as-a-service/linux/)를 따른다.

### 4.4 주소 전환 확인

1. 외부 HTTPS `/readyz`가 200인지 확인한다. 200은 Google 인증 성공을 뜻하지 않는다.
2. 새 주소에서 로그인하고 개발자 도구의 **Application → Cookies**에서 세션 쿠키가 `Secure`, `HttpOnly`, `SameSite=Lax`인지 확인한다. 쿠키 값은 복사하지 않는다.
3. **Network → WS**에서 `/ws`가 연결되는지 확인한다. 마이크 권한은 새 주소에서 다시 허용한다. 과거 주소의 쿠키·마이크 권한은 새 주소로 이전되지 않는다.
4. 초대 링크는 `scripts/pilot_invite.py --base-url https://lab.example.com`으로 새로 만든다. 저장소에 별도의 초대용 도메인 환경 설정은 없다. 링크는 일회용 비밀이므로 사람에게만 전달한다.
5. `VOINEY_LAB_APP_ORIGINS`는 **앱 자체의 정확한 Origin** 목록이다. 같은 HTTPS 페이지의 브라우저를 위해 도메인을 추가할 필요가 없다. `*`·경로·임의 Origin을 넣지 않는다. 프록시가 Host와 `X-Forwarded-Proto: https`를 보존해야 하며 서버는 루프백에 둔다.
6. 앱이 여는 서버 주소 변경은 앱 담당자에게 `https://lab.example.com`과 외부 점검 결과를 넘긴다. 앱 주소·아이콘·로고·빌드 변경은 이 안내의 작업 범위에 없다.
7. 새 주소에서 로그인·프로토콜 목록·연결 복원은 사람이 확인한다. 음성 시험은 아래 인증 점검과 별도로 비용 상한을 정해 한다.

## 5. Google 인증: 같은 설정으로 세 자격 형식

`VOINEY_LAB_GOOGLE_STT_V2_CREDENTIALS_FILE`은 그대로 쓴다. 이 파일을
google-auth의 ADC 파일 로더 한 경로로 읽는다. 지원하는 형식은 서비스 계정
`service_account`, AWS WIF `external_account`, 개발자 `authorized_user`다.
파일이 없거나 잘못되면 다른 자격·공급자로 내려가지 않는다. 셸의
`GOOGLE_APPLICATION_CREDENTIALS`로 조용히 대체하지도 않는다.

모든 파일은 저장소 밖에 두고 `chmod 600`으로 보호한다. WIF 파일 자체는
장기 비밀 키가 아니지만, 다른 엔드포인트·역할로 바뀌지 않도록 같은 권한을 적용한다.
토큰·AWS 임시 자격은 프로세스 안에서만 쓴다. 조직의 **서비스 계정 키 생성 금지**
정책을 풀 필요가 없다.

### 5.1 준비할 값

| 이름 | 어디서 확인 | 예시(반드시 바꿈) |
|---|---|---|
| Google 프로젝트 ID / 프로젝트 번호 | Google **IAM & Admin → Settings** | `my-project-id` / `123456789012` |
| AWS 계정 ID | AWS 오른쪽 위 계정 메뉴 | `111122223333` |
| EC2 역할 이름 / ARN | AWS **IAM → Roles → 역할 → Summary** | `VoineyGoogleSpeech` / `arn:aws:iam::111122223333:role/VoineyGoogleSpeech` |
| 서비스 계정 이메일 | Google **IAM & Admin → Service Accounts** | `voiney-stt@my-project-id.iam.gserviceaccount.com` |
| 풀 / 공급자 ID | 아래에서 생성 | `voiney-aws` / `ec2` |

역할 경로가 있는 ARN은 그대로 사용해 조건을 조정한다. 예시 계정 번호로 진행하지 않는다.

### 5.2 AWS 콘솔: EC2에 역할 붙이기

1. **IAM → Roles → Create role**을 누른다.
2. **Trusted entity type → AWS service**, **Use case → EC2**를 고르고 **Next**를 누른다.
3. Google 연결만을 위해 AWS 자원 접근 정책을 추가할 필요는 없다. 기존 운영 권한이 필요하면 기존 역할을 유지하고 담당자가 결정한다.
4. **Role name**에 `VoineyGoogleSpeech`를 적고 **Create role**을 누른다.
5. **EC2 → Instances → 해당 인스턴스 → Actions → Security → Modify IAM role**을 연다.
6. **IAM role**에 위 역할을 고르고 **Update IAM role**을 누른다. 인스턴스에 이미 역할이 있으면 교체 전에 기존 권한을 확인한다.
7. **Actions → Instance settings → Modify instance metadata options**에서 metadata endpoint를 켜고 **IMDSv2 → Required**로 둔다. 호스트 실행은 응답 hop limit 1로 충분하다. 컨테이너 구성은 별도 확인한다.

[EC2 역할 연결](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/attach-iam-role.html),
[IMDS 설정](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-options.html).
이 EC2의 역할 자격에 접근할 수 있는 프로세스도 Google 권한을 얻을 수 있으므로,
다른 워크로드와 역할을 공유하지 않는다.

### 5.3 Google 콘솔: 풀·AWS 공급자·서비스 계정

1. 프로젝트를 선택하고 **APIs & Services → Library → Cloud Speech-to-Text API → Enable**을 누른다. 결제 연결과 사용량 한도도 확인한다.
2. **IAM & Admin → Service Accounts**에서 기존 STT 서비스 계정을 선택하거나 **Create service account**로 만든다. **Keys**에서 새 키를 만들지 않는다.
3. **IAM & Admin → IAM → Grant access**에서 그 서비스 계정에 **Cloud Speech Client**(`roles/speech.client`)를 해당 프로젝트에 부여한다.
4. **IAM & Admin → Workload Identity Pools → Create pool**을 연다. 처음이면 IAM·STS·IAM Service Account Credentials API를 사용하도록 요구하는 화면을 따른다.
5. 풀 이름·ID를 `voiney-aws`, 상태를 **Enabled**로 하고 **Continue**를 누른다.
6. **Add provider to pool → Select a provider → AWS**를 고른다. 공급자 ID는 `ec2`, **AWS account ID**는 실제 AWS 계정 ID를 적는다.
7. **Configure provider attributes**에서 `google.subject=assertion.arn`, `attribute.aws_account=assertion.account`, `attribute.aws_role`은 아래 식으로 둔다(표현식 값만 복사한다).

   ```text
   assertion.arn.contains('assumed-role') ? assertion.arn.extract('{account_arn}assumed-role/') + 'assumed-role/' + assertion.arn.extract('assumed-role/{role_name}/') : assertion.arn
   ```

8. **Attribute condition**에 아래 식의 계정·역할을 바꿔 넣고 **Save**한다. 같은 AWS 계정의 다른 역할을 허용하지 않는다.

   ```text
   assertion.account == '111122223333' && attribute.aws_role == 'arn:aws:sts::111122223333:assumed-role/VoineyGoogleSpeech'
   ```

9. 풀의 **Grant access → Grant access using Service Account impersonation**에서 위 STT 서비스 계정을 고른다.
10. **Select principals → Only identities matching the filter**에서 `aws_role`과 위 조건의 `arn:aws:sts::...:assumed-role/VoineyGoogleSpeech` 값을 지정한다.
11. 서비스 계정의 **Permissions**에서 그 역할 필터의 principalSet에 **Workload Identity User**(`roles/iam.workloadIdentityUser`)가 부여됐는지 확인한다. 풀 전체나 모든 AWS 계정으로 넓히지 않는다.

UI가 다르면 아래 gcloud 명령을 권한 있는 관리자 셸에서 사용해 동일한 **서비스 계정**
바인딩을 만든다. `roles/iam.serviceAccountTokenCreator`를 프로젝트 전체에 주지 않는다.

```bash
GCP_PROJECT_ID='실제-프로젝트-ID'
GCP_PROJECT_NUMBER='실제-프로젝트-번호'
AWS_ACCOUNT_ID='실제-AWS-계정-ID'
AWS_ROLE_NAME='VoineyGoogleSpeech'
STT_SERVICE_ACCOUNT="voiney-stt@$GCP_PROJECT_ID.iam.gserviceaccount.com"
gcloud iam service-accounts add-iam-policy-binding "$STT_SERVICE_ACCOUNT" \
  --project="$GCP_PROJECT_ID" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$GCP_PROJECT_NUMBER/locations/global/workloadIdentityPools/voiney-aws/attribute.aws_role/arn:aws:sts::$AWS_ACCOUNT_ID:assumed-role/$AWS_ROLE_NAME"
```

[AWS federation 공식 절차](https://cloud.google.com/iam/docs/workload-identity-federation-with-other-clouds),
[WIF 권장 조건](https://cloud.google.com/iam/docs/best-practices-for-using-workload-identity-federation),
[Speech IAM](https://cloud.google.com/speech-to-text/docs/iam).

### 5.4 AWS external_account 설정 파일 만들기

Google Cloud CLI가 있는 관리자 PC 또는 Cloud Shell에서 만든다. 출력은 비밀 키가
없는 JSON이다. 서비스 계정 JSON 키를 만드는 명령과 다르다.

```bash
gcloud iam workload-identity-pools create-cred-config \
  "projects/$GCP_PROJECT_NUMBER/locations/global/workloadIdentityPools/voiney-aws/providers/ec2" \
  --service-account="$STT_SERVICE_ACCOUNT" --aws --enable-imdsv2 \
  --output-file=voiney-aws-wif.json
```

파일을 SSH 전송으로 EC2 서비스 사용자의 저장소 밖 설정 디렉터리에 옮긴다.
예시 `ubuntu` 서비스라면 `/home/ubuntu/.config/voiney/google-stt-wif.json`이다.
폴더는 700, 파일은 600이고 서비스 사용자가 소유해야 한다. WIF 파일의
`type`은 `external_account`, `credential_source.environment_id`는 `aws1`,
IMDSv2 URL이 있어야 한다. 토큰·AWS 액세스 키를 이 JSON에 넣지 않는다.
EC2에서 오래된 `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`
환경 변수를 서비스에 주지 않는다. google-auth가 EC2 역할보다 그 값을 먼저 읽는다.

서비스의 `.env`에서 **경로 한 줄**을 새 파일로 바꾼다. 나머지 STT 설정은 유지한다.
파일 내용은 출력하지 않는다.

```dotenv
VOINEY_LAB_STT_PROVIDER=google_cloud_v2
VOINEY_LAB_STT_MODEL=chirp_3
VOINEY_LAB_GOOGLE_STT_V2_PROJECT=my-project-id
VOINEY_LAB_GOOGLE_STT_V2_CREDENTIALS_FILE=/home/ubuntu/.config/voiney/google-stt-wif.json
VOINEY_LAB_GOOGLE_STT_V2_LOCATION=us
```

필요한 통신은 EC2 메타데이터(169.254.169.254), Google STS·IAM Credentials·Speech
HTTPS다. Google 라이브러리가 AWS 서명을 만들고 이를 Google STS로 교환한다.
서비스 계정 키 파일과 개발자 로그인 파일로 바꾸어도 같은 설정 이름을 쓴다.
개발자 파일은 개발 PC에서 `gcloud auth application-default login`으로 만들고
`gcloud auth application-default set-quota-project "$GCP_PROJECT_ID"`로 quota
프로젝트를 지정한다. 개발자는 그 프로젝트에서 필요한 API 사용·Speech 권한이
있어야 한다. EC2 운영에는 개발자 refresh token을 배치하지 않는다.

### 5.5 확인과 옛 키 폐기

1. 실험이 없는 때 `sudo systemctl restart voineylab.service`를 실행한다.
2. `curl --fail --max-time 10 http://127.0.0.1:8080/readyz`로 로컬 설정을 확인한다. **인증 요청 0회**, Speech 요청 0회다.
3. 인증만 확인할 때는 아래 명령을 서비스 사용자로 실행한다. 설정 파일을 직접 지정하므로 `.env`를 출력하지 않는다. 토큰은 출력하지 않는다. 최초 인증은 IMDSv2 조회 여러 번·Google STS 1회·서비스 계정 impersonation 1회가 필요할 수 있다. 자동 반복하지 않고 최대 30초로 끊는다. Speech 호출 비용은 0이다.

   ```bash
   cd /opt/voineylab
   sudo -u voineylab env \
     VOINEY_LAB_GOOGLE_STT_V2_PROJECT=my-project-id \
     VOINEY_LAB_GOOGLE_STT_V2_CREDENTIALS_FILE=/var/lib/voineylab/.config/voiney/google-stt-wif.json \
     timeout 30s .venv/bin/python scripts/check_google_stt_auth.py
   ```

4. 사람이 공개된 비실험 문장으로 한 번 음성 시험한다. 녹음 비교 명령은 [문제 해결 안내](TROUBLESHOOTING_GUIDE.md) 6절. 공급자·현재 가격을 확인하고 최대 1분 등 요청 상한을 먼저 정한다. 인증 성공과 실제 Speech 권한·지역·인식 품질은 별개다.
5. 성공 후 Google **IAM & Admin → Service Accounts → 해당 계정 → Keys**에서 **옛 키 ID**를 확인해 먼저 **Disable**한다. 서비스 재시작 후 다시 인증·짧은 인식 확인을 한다(기존 메모리 토큰만으로 확인하지 않는다).
6. 키 비활성화 상태로도 성공하면 같은 화면에서 그 키를 **Delete**한다. 다른 시스템이 쓰는 키는 삭제하지 않는다. 조직 키 생성 금지 정책이 원래대로인지 관리자가 확인한다.
7. EC2의 옛 `google-stt.json`과 다운로드·관리 PC의 복사본은 사람이 정확한 경로를 확인해 삭제한다. 디스크 블록까지 지워졌다는 뜻은 아니다. 장애 복구는 WIF 권한·설정으로 하고 새 키를 만들기 위해 정책을 풀지 않는다.

## 6. 매일 백업 — 사람이 켤 때만

기존 `scripts/pilot_state_backup.py`의 검증·복구 형식을 재사용한다. 계정·작업공간·
프로토콜 카탈로그·실험 기록 네 DB가 있어야 일일 작업이 시작된다. 선택한 데이터
경로의 다른 SQLite(기관 안전 카탈로그·지표 포함)와 허용한 원문·첨부도 담는다.
`.env`·Google 자격·터널 자격·원음·진단 글은 담지 않는다.

백업은 서버와 **다른 승인된 암호화 볼륨**에 두는 것을 권한다. 같은 EC2 디스크의
묶음만으로는 디스크·계정 장애에 대비할 수 없다. 외부 저장소 업로드·보관 기한과
삭제 정책은 사람이 정한다. 이 스크립트는 자동 삭제나 외부 업로드를 하지 않는다.
임시 검사 공간에도 데이터가 풀리므로 `/tmp`가 있는 볼륨도 보호한다.

`deploy/voineylab-backup.service`의 경로·사용자를 실제 서버와 맞춘다.
백업 경로는 서버·데이터 폴더와 별도의 전용 폴더로 고른다. 심볼릭 링크나
`..`가 섞인 경로, 서버·데이터의 내부나 상위 폴더는 일일 스크립트가 거절한다.
운영 데이터가 `data/runtime/pilot` 밖이면 실제 네 경로로 바꾼다. 매번 파일럿
실행기가 쓴 저장소와 일치해야 한다. 아래는 실제로 적용하려는 사람이 실행한다.

```bash
cd /opt/voineylab
sudo install -d -o root -g root -m 755 /usr/local/libexec
sudo install -o root -g root -m 755 scripts/daily_backup.sh /usr/local/libexec/voineylab-daily-backup
sudo install -m 644 deploy/voineylab-backup.service /etc/systemd/system/voineylab-backup.service
sudo install -m 644 deploy/voineylab-backup.timer /etc/systemd/system/voineylab-backup.timer
sudo systemctl daemon-reload
# 먼저 실험이 없는 시간에 한 번 실행한다. 잠깐 서버 연결이 끊긴다.
sudo systemctl start voineylab-backup.service
sudo systemctl is-active voineylab.service
curl --fail --max-time 10 http://127.0.0.1:8080/readyz
```

스크립트는 서버가 이미 꺼져 있으면 실패하며 켜지 않는다. 실행 중일 때만 멈추고,
백업 생성·검증 성공 여부와 관계없이 다시 시작을 시도한다. 재시작 실패는 작업
실패로 남으므로 운영자가 확인한다. 백업 이름은 UTC 시각·프로세스 번호로 구분하고
덮어쓰지 않는다. 파일은 소유자만 접근한다. root 소유로 설치한 스크립트 자체를
읽기 전용으로 잠가 같은 설치의 중복 실행을 막는다. 사용자 쓰기 가능 폴더의
잠금 파일을 열어 다른 파일을 덮어쓰지 않는다.

복구 연습까지 성공한 뒤 **자동 실행을 원하는 사람만** 다음을 실행한다.

```bash
sudo systemctl enable --now voineylab-backup.timer
systemctl list-timers voineylab-backup.timer
```

기본은 UTC 18:30(한국 다음 날 03:30)이므로 실험 시간과 겹치면 **켜기 전에** timer의
`OnCalendar`를 바꾼다. 부팅 직후 놓친 백업을 따라 실행하지 않는다(`Persistent=false`).
끄기: `sudo systemctl disable --now voineylab-backup.timer`.

### 복구 연습과 실제 복구

아래 `ARCHIVE`는 실제 묶음의 절대 경로로 바꾼다. 운영 폴더에 바로 풀지 않는다.

```bash
cd /opt/voineylab
ARCHIVE='/var/backups/voineylab/선택한-백업.tar.gz'
sudo -u voineylab .venv/bin/python scripts/pilot_state_backup.py verify "$ARCHIVE"
sudo -u voineylab .venv/bin/python scripts/pilot_state_backup.py restore "$ARCHIVE" /var/lib/voineylab/restore-drill-001
```

대상 폴더는 **아직 없어야** 하고 부모는 서비스 사용자가 쓸 수 있어야 한다.
복구 결과는 `protocol/`, `workspace/`, `reports/`로 분리된다. 다음 명령은
그것을 **새 폴더**에 파일럿 실행기가 쓰는 배치로 합친다. 기존 폴더는 덮지 않는다.

```bash
sudo -u voineylab bash -eu <<'RESTORE_LAYOUT'
restored_dir=/var/lib/voineylab/restore-drill-001
staged_pilot=/var/lib/voineylab/pilot-layout-001
test ! -e "$staged_pilot"
test ! -L "$staged_pilot"
cp -a "$restored_dir/protocol" "$staged_pilot"
cp -a "$restored_dir/workspace" "$staged_pilot/workspace"
cp -a "$restored_dir/reports/experiment_reports.sqlite" "$staged_pilot/experiment_reports.sqlite"
RESTORE_LAYOUT
```

별도로 준비한 격리된 시험 설치의 `data/runtime/pilot/`에 이 배치를 넣고,
그 설치에서 `PORT=8081 ./scripts/run_pilot.sh`로 확인한다. 운영 설치와 같은
데이터 폴더·포트를 쓰지 않는다. `run_pilot.sh`는 데이터 경로를 고정하므로
복구 경로 환경 변수만 바꿔서는 안 된다. 시험 설치에는 서비스 사용자만 접근할 수
있는 설정을 준비하고 외부 모델/음성 요청이 생기는 분석·음성 실행은 하지 않는다.
`/readyz`만 성공했다고 복구가 끝난 것은 아니다.

복구한 계정 로그인, 프로토콜 목록·원문 첨부, 실험 기록과 보고서 읽기를 확인한다.
복구 직후 과거 타이머·실험을 완료된 것으로 추정하지 않는다. 실제 전환은 사람의
정비 시간에만 한다. 자동 배포·백업이 진행 중이면 끝날 때까지 기다린다.
아래는 설치가 예시 경로와 정확히 일치하고, 검증한 별도 배치가 준비된 경우다.

```bash
sudo bash -eu <<'RESTORE_SWITCH'
live_pilot=/opt/voineylab/data/runtime/pilot
saved_pilot=/opt/voineylab/data/runtime/pilot-before-restore-001
staged_pilot=/var/lib/voineylab/pilot-layout-001
test -d "$live_pilot"
test -d "$staged_pilot"
test ! -e "$saved_pilot"
test ! -L "$saved_pilot"
systemctl disable --now voineylab-backup.timer
systemctl stop voineylab.service
mv "$live_pilot" "$saved_pilot"
cp -a "$staged_pilot" "$live_pilot"
chown -R voineylab:voineylab "$live_pilot"
systemctl start voineylab.service
curl --fail --max-time 10 http://127.0.0.1:8080/readyz
RESTORE_SWITCH
```

어느 명령이든 실패하면 다음 단계로 가지 않고 보존한 폴더를 유지한다. 옛 `-wal`·
`-shm` 파일을 새 DB 옆에 섞지 않는다. 로컬/외부 `/readyz`와 위 읽기 확인 뒤
사람에게 열고 원할 때만 백업 타이머를 다시 켠다. 옛 폴더는 확인 전 지우지 않는다.
현재 서버에서 이 전환·재부팅·실제 데이터 복구는 확인 안 됨이다.
