# 룰렛 보상 봇 — 세팅 가이드

## 1. Python 설치
https://www.python.org/downloads/ 에서 Python 3.10 이상 설치
설치 시 "Add to PATH" 반드시 체크

## 2. 라이브러리 설치
이 폴더에서 cmd(명령 프롬프트) 열고 입력:
```
pip install -r requirements.txt
```

## 3. 디스코드 봇 만들기
1. https://discord.com/developers/applications 접속
2. "New Application" → 이름 입력
3. 왼쪽 "Bot" 탭 → "Add Bot"
4. "TOKEN" → "Reset Token" → 복사
5. token.txt 파일에 붙여넣기 (bot.py 와 같은 폴더)

## 4. 봇 권한 설정 (중요)
Bot 탭에서 아래 항목 활성화:
- MESSAGE CONTENT INTENT ✅
- SERVER MEMBERS INTENT ✅

OAuth2 → URL Generator:
- SCOPES: bot, applications.commands 체크
- BOT PERMISSIONS: Send Messages, Read Message History, Embed Links 체크
- 생성된 URL로 봇을 서버에 초대

## 5. config.json 설정
- `embed_color`: HEX 색상 (# 없이, 예: "5865F2")
- `rewards`: 보상 항목 목록
- `users` 각 항목:
  - `name`: 유저 닉네임
  - `thread_id`: 포스트 스레드 ID (아래 참고)
  - `message_id`: 수정할 메시지 ID (처음엔 빈칸 "" 으로 두면 자동 전송 후 저장됨 — 단, 자동저장은 /전체업데이트 후 config.json 에서 직접 메시지ID 확인 후 입력 필요)

### 스레드 ID 가져오는 방법
디스코드 설정 → 고급 → 개발자 모드 ON
포스트(스레드) 이름 우클릭 → "ID 복사"

### 메시지 ID 가져오는 방법
스레드 안 메시지 우클릭 → "ID 복사"
처음 실행 시 message_id 를 비워두면 봇이 새 메시지를 전송하고
그 메시지 ID를 복사해서 config.json 에 직접 입력하세요.

## 6. 봇 실행
```
python bot.py
```
"봇 온라인" 메시지가 뜨면 성공!

## 7. 슬래시 명령어 목록
| 명령어 | 설명 |
|---|---|
| /차감 | 특정 유저 특정 보상 개수 변경 + 메시지 자동 수정 |
| /전체업데이트 | 전체 유저 포스트 메시지 일괄 업데이트 |
| /보상항목추가 | 새 보상 항목 추가 + 전체 자동 업데이트 |
| /보상현황 | 특정 유저 현재 보상 현황 확인 |
| /유저추가 | 새 유저를 config에 추가 |
| /유저목록 | config에 등록된 유저 닉네임 목록 확인 |
| /유저삭제 | 닉네임 기준으로 기존 유저를 config에서 삭제 |
| /일괄추가 | 위플랩 룰렛 목록 복사 내용을 읽어 보상을 한 번에 추가 |

## 사용 예시
위플랩 룰렛후원목록 일괄 반영:
→ 위플랩 표에서 여러 줄을 복사한 뒤 /일괄추가 내용:에 그대로 붙여넣기
→ 등록된 닉네임과 보상명을 찾아 자동으로 개수를 더하고 포스트 메시지 업데이트

새 보상 항목 "연속시청권" 추가:
→ /보상항목추가 항목이름:연속시청권
→ 전체 유저 포스트 메시지 자동 업데이트 완료

특정 유저 선참권 3개로 변경:
→ /차감 닉네임:전설의힐러6707 보상이름:선참권 개수:3
→ 해당 유저 포스트 메시지 자동 수정 완료
