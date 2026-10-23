# Android 도구 0.2.0 업데이트

NAIA v2.0.48의 GitHub 설치기는 같은 확장 ID가 이미 있으면 교체를 거부합니다.
화면에 있는 업데이트 버튼도 같은 설치기를 호출하므로, 이 버전에서는 정상적인
덮어쓰기 업데이트로 사용할 수 없습니다. 이 확장은 그 한 번의 교체만 담당합니다.

## 설치 및 적용

1. 기존 NAIA의 Settings → Extension에서 아래 GitHub 주소를 설치합니다.
   `https://github.com/ugi8296-droid/naia-android-string-compat/tree/android-tools-update`
2. **Android 도구 0.2.0 업데이트**를 신뢰하고 활성화합니다.
3. 같은 Extension 설정에서 **Android 도구 0.2.0 적용**을 한 번 누릅니다.
4. 완료 안내가 뜨면 안드로이드 설정에서 NAIA를 강제 종료하고 다시 실행합니다.
5. Settings → Extension에서 기존 **NAIA Android 도구 0.2.0**을 다시 신뢰하고 활성화합니다.
6. Settings → Global → Android 도구 · 플러그인에서 새 화면 조회·조작 권한을 원하는 범위만 켭니다.
7. 업데이트용 확장은 작업 완료 뒤 꺼 두어도 됩니다. 다시 눌러도 이미 적용되었다고 표시됩니다.

APK 재설치나 연결 파일 재등록은 필요 없습니다. 원래 도구의 ID는 그대로입니다.
새 화면 조회·화면 조작 권한은 자동으로 켜지지 않습니다. 이 업데이트 확장 자체는
화면 캡처, 플러그인 연결, 이미지 생성 또는 인증 정보 조회 기능을 갖고 있지 않습니다.

## 교체 범위

- `naia_zz_android_tools_v1`의 0.1.0만 0.2.0으로 바꿉니다.
- 묶음의 전체 SHA-256, 14개 파일의 개별 SHA-256, 경로와 매니페스트를 검증합니다.
- `settings.json`을 보존하고 새 `allow_screen`, `allow_control` 값은 꺼 둡니다.
- 앱 전용 `config/android-tools/pairing.json`과 명령 기록은 건드리지 않습니다.
- 원래 확장은 중지하고 승인을 해제합니다. 새 코드는 이 도구에서 가져오거나 실행하지 않습니다.
- 기존 파일은 확장 검색 폴더 밖의 `config/android-tools/update-0.2.0/previous-extension`에 보관합니다.
- 교체 중 실패하면 이전 파일을 복원합니다. 이미 중지된 실행부는 재시작과 재승인이 필요합니다.

## 빌드

테스트를 마친 Android 도구 0.2.0 소스가 준비되면 `python build_payload.py`를 실행합니다.
배포에는 `main.py`, `extension.json`, `README.md`, `payload-manifest.json`,
`android-tools-0.2.0.zip`을 포함합니다. 내부 ZIP을 풀어서 저장소에 배치하지 마세요.
원본 설치기가 여러 `extension.json`을 발견하면 설치를 거부합니다.
