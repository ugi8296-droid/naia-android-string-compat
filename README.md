# NAIA ChatGPT 구독 연결 · 0.1.1 미리보기

기존 NAIA Android 앱의 AI Assist와 Auto Boost를 공식 **ChatGPT로 로그인** 방식에 연결하는 확장입니다. 별도 OpenAI API 키나 Termux를 설치하지 않습니다. 이미지 생성은 기존 NovelAI 연결을 사용합니다.

## 설치

1. 이전 **NAIA ChatGPT 구독 연결 (미리보기)**가 설치되어 있다면 **Settings → Extension**에서 먼저 끕니다. 기존 문자열 호환성 패치는 그대로 켜 두세요.
2. 같은 화면의 GitHub 주소 입력란에 아래 주소를 붙여 넣고 **GitHub에서 설치**를 누릅니다.

   `https://github.com/ugi8296-droid/naia-android-string-compat/tree/chatgpt-connect`

3. 새 **NAIA ChatGPT 구독 연결 0.1.1 (미리보기)**를 켜고 **신뢰하고 활성화**를 누릅니다.
4. 앱을 완전히 종료한 뒤 다시 엽니다. **Settings → Global → ChatGPT 연결**을 누릅니다. 앱을 삭제하거나 앱 데이터를 지우지 마세요.
5. 연결되지 않았다면 **ChatGPT로 로그인**을 누릅니다. 외부 브라우저에서 본인 계정으로 로그인하고 ChatGPT 사용 권한을 승인한 뒤 NAIA로 돌아옵니다. 브라우저 자동 열기가 지원되지 않으면 **로그인 링크 복사**로 주소를 복사해 Chrome 등에 붙여 넣습니다.
6. **목록 새로고침 → 사용할 모델 선택 → 이 모델 사용**을 누르고 **Assist · Auto Boost에 ChatGPT 사용**을 켭니다. 사용 가능한 모델은 로그인한 계정에서 조회합니다.
7. 기존 AI Assist와 Auto Boost 기능을 사용합니다. 이 확장은 로그인이나 모델 선택만으로 AI 요청을 시작하지 않습니다.

NAIA v2.0.48의 GitHub 설치기는 같은 확장 ID를 덮어쓰지 못합니다. 따라서 0.1.1은 새 ID `naia_chatgpt_connect_v2`로 설치되며 이전 `naia_chatgpt_connect_v1`은 꺼 둡니다. 이미 0.1.1을 설치했다면 같은 주소를 다시 설치할 필요가 없습니다.

로그인 저장 위치 `config/chatgpt-auth`는 이전 버전과 같습니다. 앱 데이터가 유지되면 기존 로그인 정보를 다시 사용하지만, 이전 확장의 모델 선택·사용 스위치 설정은 자동으로 옮기지 않으므로 모델을 다시 선택하고 사용을 켜 주세요. 해제는 새 연결 화면에서 **연결 해제**, 기능 제거는 Settings에서 확장을 끈 뒤 앱을 완전히 종료하고 다시 시작합니다.

## 이용 한도와 연결

- 내부 통신에는 공식 Responses API를 사용하지만, 별도 API 키 대신 로그인한 ChatGPT 계정의 사용 권한을 사용합니다.
- ChatGPT 이용 한도가 적용됩니다. 추가 크레딧 사용을 원하지 않으면 **ChatGPT의 이 앱 연결 설정에서 추가 크레딧 사용을 꺼 주세요.** 이 확장이 계정의 결제 설정을 바꾸지는 않습니다.
- 이 대화의 내용이나 ChatGPT의 기존 기억이 자동으로 전달되지 않습니다. NAIA가 현재 요청에 필요한 프롬프트와 문맥을 전송합니다.
- 응답 거절, 한도 오류, 연결 끊김, 미완료 응답은 적용하지 않습니다. 실패한 AI 생성 요청을 자동 재전송하지 않습니다.
- 로그인 정보는 앱 비공개 폴더에 저장하며 일반 확장 설정이나 진단 메시지에 토큰을 넣지 않습니다. 앱 수준의 별도 키 저장소 암호화는 구현하지 않았습니다. 연결 해제는 로컬 토큰을 삭제하고 서버 폐기를 시도합니다.

## 호환 범위와 검증 한계

NAIA v2.0.48의 원본 AI 모듈과 웹 화면을 대상으로 합니다. 버전별 내부 연결 지점을 사용하므로 예상한 코드와 다르면 적용을 중단합니다. 원본 소스 파일은 수정하지 않으며 확장을 끄고 앱을 다시 시작하면 원래 동작으로 돌아갑니다.

호스트에서 OAuth 보안, 실제 루프백 콜백, 응답 스트림 오류, 모든 Assist 출력 형식, Boost 적용, 이미 등록된 API 경로, 실제 확장 설치·승인을 검사했습니다. 원격 인증과 AI 응답은 모의 서버/응답으로 검증했습니다. 실제 ChatGPT 계정 로그인·추론·과금 요청은 실행하지 않았습니다.

**실제 Galaxy/Android 실행은 아직 검증하지 않았습니다.** 기존 APK의 브라우저 실행, 백그라운드 유지, 앱 비공개 경로, 내장 인증 라이브러리와 웹 화면 호환성을 최초 기기 사용에서 확인해야 합니다. 이 작업 환경의 Chrome은 검사 서버에 접속하지 못해 화면의 시각 검증도 완료하지 못했습니다. 따라서 이 버전은 검증된 완성 APK가 아닌 설치 가능한 확장 미리보기입니다.

## 공식 연결 규격

- [로그인 및 등록](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [모델 조회 및 추론](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [계정과 세션](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)

GPL-3.0. NAIA 및 OpenAI의 공식 배포물이나 보증을 의미하지 않습니다.
