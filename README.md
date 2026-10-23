# NAIA Android 복구·업데이트

최신 Android 도구는 **0.2.4**입니다. 이미 이 복구 도구가 설치되어 있다면 **GitHub 최신 수정본 확인 → 확인한 수정본 적용 → 수정한 확장 다시 불러오기** 후 NAIA 화면을 새로고침하세요. 확장을 다시 설치할 필요가 없습니다. 0.2.4는 화면 조회·무관한 터치에 따른 메뉴 클릭 무효화와 전체/간편 화면 전환 인식을 수정합니다. 적용 후 화면 새로고침이 필요합니다. 서버의 화면 응답 거절 수정도 배포되어 있습니다.

기존 Android 도구 0.2.0에서 발생한 `screen_service` 오류와 ChatGPT 연결·저장 위치 확장의 중복 등록 오류를 복구합니다. 기존 앱, 연결 정보, 설정과 확장 ID를 유지합니다.

## 설치와 적용

1. 기존 NAIA의 **Settings → Extension**에서 이 주소를 GitHub 설치에 입력합니다.
   `https://github.com/ugi8296-droid/naia-android-string-compat/tree/android-tools-repair`
2. **NAIA Android 복구·업데이트**를 신뢰하고 활성화합니다. 이 복구 확장 설치는 한 번만 필요합니다.
3. 패널의 **내장 0.2.4 수정본 적용**을 누르고 적용 완료를 기다립니다.
4. **수정한 확장 다시 불러오기**를 누릅니다. 각 확장의 **불러오기 완료** 또는 오류 결과를 확인합니다.
5. NAIA 화면을 새로고침합니다.

파일 적용과 실행 성공은 별도로 표시합니다. 기존에 꺼 두거나 차단한 확장은 켜지지 않습니다. 앱 삭제, 데이터 삭제, 다른 APK 설치는 필요하지 않습니다. 이전 업데이트 도구는 새 버전 설치에 사용하지 않습니다.

## 이후 수정본

같은 복구 패널의 **GitHub 최신 수정본 확인 → 확인한 수정본 적용 → 수정한 확장 다시 불러오기**를 사용합니다. 확인 결과는 10분 동안 유효합니다. 자동 다운로드·적용·생성은 하지 않습니다. 업데이트 확인은 고정 저장소의 `android-tools-repair` 브랜치를 정확한 커밋으로 고정한 후 파일을 받습니다. 임의 주소·리디렉션·이전 버전은 받지 않습니다.

## 보존 및 복구 범위

- Android 도구 `naia_zz_android_tools_v1`: 0.1.0/0.2.0을 0.2.1로 교체하고, 파일이 섞인 동일 0.2.1도 복구합니다.
- ChatGPT 연결 `naia_chatgpt_connect_v2`: 검증된 0.1.1 소스만 0.1.2로 교체합니다.
- 저장 위치 `naia_storage_picker_v1`: 검증된 0.1.0 소스만 0.1.1로 교체합니다.
- 차단된 옛 ChatGPT v1은 수정하거나 활성화하지 않습니다. 다른 소스가 설치된 경우 이를 표시하고 건너뜁니다.
- 기존 승인·활성·작동·차단 설정, 저장 위치, OAuth·페어링 파일을 유지합니다. 화면·조작·수정·생성 권한을 추가로 켜지 않습니다.
- 기존 파일을 먼저 `config/android-tools/repair-backups/`에 백업하고, 모든 파일을 검증한 뒤 교체합니다. 중간 실패 시 기존 디렉터리를 복원하며, 강제 종료된 작업은 다음 적용 때 복구합니다.

## 소스와 재현

NAIA v2.0.48 기반 확장 API 1용입니다. 원본 저장소는 `DNT-LAB/NAIA2.0`이며 이번 호스트 검증 기준 커밋은 `6e97c7f`로 시작합니다. 수정본 소스는 같은 `ugi8296-droid/naia-android-string-compat` 저장소의 `android-tools`, `chatgpt-connect`, `storage-picker` 브랜치에서 관리합니다. 각 배포 소스의 정확한 바이트는 `release-manifest.json`에 기록되어 있습니다. 원본·수정 해시는 `build-inputs/`에 포함됩니다.

작업 폴더에서는 `python build_payload.py`로 묶음을 만듭니다. 별도 체크아웃은 아래처럼 지정합니다.

```sh
python build_payload.py --android-tools ../android-tools --chatgpt ../chatgpt-connect --storage ../storage-picker --original-hashes build-inputs/original-hashes.json --replacement-hashes build-inputs/replacement-hashes.json
python -m unittest discover -s tests
```

빌드는 네트워크에 접속하지 않습니다. 입력 해시를 검증하고 정렬된 ZIP과 JSON, 엔트리의 두 SHA-256 상수를 갱신합니다. 런타임 원본 테스트는 상위 작업 프로젝트의 `repair-tests/`에서 별도로 수행합니다. 공개 패키지는 계정 정보나 실제 연결 파일을 포함하지 않습니다.

GPL-3.0 라이선스이며 포함된 html2canvas는 해당 MIT 고지를 유지합니다.
