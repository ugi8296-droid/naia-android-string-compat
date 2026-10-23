# NAIA ChatGPT 구독 연결 · 0.1.2 미리보기

기존 NAIA Android 앱의 AI Assist와 Auto Boost를 공식 **ChatGPT로 로그인** 방식에 연결하는 확장입니다. 별도 OpenAI API 키나 Termux를 설치하지 않습니다. 이미지 생성은 기존 NovelAI 연결을 사용합니다.

## 기존 설치 복구 · 업데이트

이미 0.1.1을 설치했다면 같은 확장을 다시 설치하지 않습니다. 기존 앱의 **Settings → Extension → GitHub에서 설치**에 다음 복구 도구 주소를 입력합니다.

`https://github.com/ugi8296-droid/naia-android-string-compat/tree/android-tools-repair`

1. **NAIA Android 복구·업데이트**를 활성화하고 코드 실행을 승인합니다.
2. 복구 도구에서 **내장 0.2.1 수정본 적용**을 누릅니다. Android 도구 0.2.1과 함께 기존 ChatGPT 연결 0.1.1을 확인해 0.1.2로 수정합니다.
3. **수정한 확장 다시 불러오기**를 누르고 NAIA 화면을 새로고침합니다. 실행부 재시작이 필요하다고 표시되면 앱을 완전히 종료한 뒤 다시 엽니다.
4. **Settings → Global → ChatGPT 연결**에서 기존 연결과 모델 선택을 확인합니다. 이전 `naia_chatgpt_connect_v1` 확장은 꺼 두거나 차단한 상태를 유지합니다.

앱 삭제·재설치나 데이터 초기화는 필요 없습니다. 확장 ID `naia_chatgpt_connect_v2`, `config/chatgpt-auth`의 로그인 정보, 모델·사용 스위치 설정을 유지합니다. 설치된 파일이 확인한 원본과 다르면 해당 항목을 건너뛰고 결과에 표시합니다. 이후에는 같은 복구 도구의 **GitHub 최신 수정본 확인 → 확인한 수정본 적용**으로 업데이트합니다.

## 0.1.2 수정 사항

- 확장을 다시 불러올 때 남아 있던 연결 실행부를 정리해 `ChatGPT bridge is already installed` 오류를 고칩니다.
- 파일을 업데이트해도 이전 Python 모듈·바이트코드가 계속 실행되던 문제를 고칩니다.
- 차단한 이전 v1 연결을 다시 켜지 않고 남아 있는 실행부만 정리합니다.
- 설정 화면을 다른 확장과 함께 구성하고, 반복해서 불러와도 버튼과 화면 수정이 중복되지 않게 합니다.

## 처음 설치하는 경우

ChatGPT 연결을 한 번도 설치하지 않았다면 `https://github.com/ugi8296-droid/naia-android-string-compat/tree/chatgpt-connect`를 사용합니다. 활성화 후 **Settings → Global → ChatGPT 연결**에서 로그인하고 **목록 새로고침 → 사용할 모델 선택 → 이 모델 사용**을 누릅니다. **Assist · Auto Boost에 ChatGPT 사용**을 켜면 기존 AI Assist와 Auto Boost가 선택한 모델을 사용합니다. 로그인이나 모델 선택만으로 AI 요청을 시작하지 않습니다.

브라우저 자동 열기가 지원되지 않으면 **로그인 링크 복사**를 사용합니다. 연결 해제는 연결 화면의 **연결 해제**에서 할 수 있습니다.

## 이용 한도와 연결

- 내부 통신에는 공식 Responses API를 사용하지만, 별도 API 키 대신 로그인한 ChatGPT 계정의 사용 권한을 사용합니다.
- ChatGPT 이용 한도가 적용됩니다. 추가 크레딧 사용을 원하지 않으면 **ChatGPT의 이 앱 연결 설정에서 추가 크레딧 사용을 꺼 주세요.** 이 확장이 계정의 결제 설정을 바꾸지는 않습니다.
- 이 대화의 내용이나 ChatGPT의 기존 기억이 자동으로 전달되지 않습니다. NAIA가 현재 요청에 필요한 프롬프트와 문맥을 전송합니다.
- 응답 거절, 한도 오류, 연결 끊김, 미완료 응답은 적용하지 않습니다. 실패한 AI 생성 요청을 자동 재전송하지 않습니다.
- 로그인 정보는 앱 비공개 폴더에 저장하며 일반 확장 설정이나 진단 메시지에 토큰을 넣지 않습니다. 앱 수준의 별도 키 저장소 암호화는 구현하지 않았습니다. 연결 해제는 로컬 토큰을 삭제하고 서버 폐기를 시도합니다.

## 호환 범위와 검증 한계

NAIA v2.0.48의 원본 AI 모듈과 웹 화면을 대상으로 합니다. 버전별 내부 연결 지점을 사용하므로 예상한 코드와 다르면 적용을 중단합니다. 원본 소스 파일은 수정하지 않으며 확장을 끄고 앱을 다시 시작하면 원래 동작으로 돌아갑니다.

호스트에서 OAuth 보안, 실제 루프백 콜백, 응답 스트림 오류, 모든 Assist 출력 형식, Boost 적용, 이미 등록된 API 경로, 실제 확장 설치·승인을 검사했습니다. 원격 인증과 AI 응답은 모의 서버/응답으로 검증했습니다. 실제 ChatGPT 계정 로그인·추론·과금 요청은 실행하지 않았습니다.

0.1.2에서는 원본 확장 관리자를 통해 기존 중복 설치 오류를 재현하고, 여러 확장의 반복 재시도·화면 합성·저장된 연결 정보 보존을 오프라인으로 검증했습니다. 같은 크기·수정 시각의 소스 교체, 차단한 v1 정리, 수정한 화면 모듈의 JavaScript 문법도 검사했습니다.

**실제 Galaxy/Android 실행은 아직 검증하지 않았습니다.** 기존 APK의 브라우저 실행, 백그라운드 유지, 앱 비공개 경로, 내장 인증 라이브러리와 웹 화면 호환성을 최초 기기 사용에서 확인해야 합니다. 이 작업 환경의 Chrome은 검사 서버에 접속하지 못해 화면의 시각 검증도 완료하지 못했습니다. 따라서 이 버전은 검증된 완성 APK가 아닌 설치 가능한 확장 미리보기입니다.

## 공식 연결 규격

- [로그인 및 등록](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [모델 조회 및 추론](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [계정과 세션](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)

GPL-3.0. NAIA 및 OpenAI의 공식 배포물이나 보증을 의미하지 않습니다.
