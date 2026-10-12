# 파일 스캐너 (Playground File Scanner) — 코드 흐름 문서

이 문서는 `apps/`(FastAPI 라우터)와 `lib/`(도메인 로직)를 중심으로 각 엔드포인트가
내부적으로 어떤 함수를 어떤 인자(전달자)와 함께 호출하는지 상세히 정리한 것입니다.
**Playground Core Manager** 구조에 맞춰 리팩토링했으며 추후 그 프로젝트와 통합할 예정입니다.

## 1. 시스템 개요

`parser-config.yaml`의 `files.initialdir` 파일을 스캔해 sqlite에 기록하고,
파일별 타입/필드/그룹을 관리하는 Web UI 백엔드입니다. 주요 책임:

1. **설정 관리**: `parser-config.yaml`(+로컬 override) 로딩, 런타임 override/재로딩.
2. **스캔**: 파일 트리 스캔 → 최초발견/마지막변경 기록, 전체 상세 재조회·변경기록.
3. **파일 상세**: 타입 자동 판정(csv/json/xml/txt), 필드 keep/rename/tag, 파서 지정.
4. **그룹**: 이름 변경, regex 일괄 지정, 그룹 상세(기준 파일), 그룹 합치기(기준 파일 선택).

## 2. 외부 시스템 의존

| 외부 시스템 | 용도 | 관련 모듈 |
| --- | --- | --- |
| 파일 시스템 (initialdir) | 스캔 대상 파일 트리 | `lib/file_store.py`, `lib/scanner.py` |
| SQLite | files/file_fields/file_meta/groups 저장 | `lib/db.py` |
| parser-config.yaml | 베이스 설정 (GitLab 베이스 config 대응) | `lib/config_loader.py` |

## 3. 애플리케이션 부팅 흐름 (main.py)

```
main.py 실행
  │
  ├─ 환경변수 로드: REVISION → env.{revision} → load_dotenv()
  ├─ 로깅 설정 (LOG_LEVEL / LOG_FORMAT)
  │
  ├─ load_config()                              # lib/config_loader.py
  │     ├─ parser-config.yaml 읽기
  │     └─ parser-config.local.yaml 과 dict_deep_merge  # override 병합
  │
  ├─ config 를 각 라우터 모듈 전역에 주입
  │     ui.config / configuration.config / scan.config / files.config / groups.config = config
  │
  ├─ FastAPI 앱 생성 (docs_url, redoc_url, openapi_url 에 CONTEXT_PATH 접두)
  │
  └─ include_router(prefix=context_path) 로 라우터 등록
        ui / configuration / scan / files / groups

참고: 각 라우터는 런타임에 설정을 lib/runtime_config.get_config()로 다시 읽습니다.
main.py가 모듈 전역에 주입하는 *.config는 실제 핸들러 로직에서는 거의 사용되지 않고,
핸들러는 get_config()(런타임 캐시)를 통해 일관된 설정을 사용합니다.
```

실행:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

브라우저: <http://localhost:8000/>
설정 파일을 다른 위치에 두려면 `PARSER_CONFIG=/path/to/parser-config.yaml` 환경변수를 사용하세요.

## 4. 라우터 ↔ 엔드포인트 매핑

모든 엔드포인트는 `CONTEXT_PATH` 접두사 아래에 등록되며, 대부분 GET/POST 양쪽을 동시에 지원합니다.
API 엔드포인트는 `/api` 접두 아래에 있다.

| 라우터 모듈 | 엔드포인트 | 설명 | 호출 대상 (lib) |
| --- | --- | --- | --- |
| `apps/ui.py` | `GET /` | 단일 HTML 페이지 (버튼 → 아래 API 호출) | - |
| `apps/configuration.py` | `/api/reload` | 설정 재로딩 | `runtime_config.reload_config()` |
| | `/api/config_override` | 런타임 config 부분 병합 | `runtime_config.apply_override(override)` |
| | `/api/config` | 현재 런타임 config 조회 | `runtime_config.get_config()` |
| `apps/scan.py` | `/api/scan` | initialdir 스캔 (files upsert) | `file_store.scan_directory(db)` |
| | `/api/scan-all` | 전체 스캔·변경기록 (상세 재조회) | `file_store.scan_all(db)` |
| `apps/files.py` | `/api/files` | 파일 목록 (그룹 필터) | `file_store.list_files(db, group)` |
| | `/api/parsers` | 파서 이름 목록 | `get_config().parser_names()` |
| | `/api/file/detail` | 파일 상세 (최초 진입 시 DB 기록) | `file_store.get_file_detail(db, file_id)` |
| | `/api/file/refresh` | 파일 재읽기·갱신 (설정 보존) | `file_store.refresh_file(db, file_id)` |
| | `/api/file/save` | 상세 변경 저장 | `file_store.save_file_settings(db, file_id, ...)` |
| `apps/groups.py` | `/api/groups` | 그룹 목록 | `db.distinct_groups()` |
| | `/api/group/rename` | 그룹명 변경 | `file_store.rename_group(db, group_name, new_group_name)` |
| | `/api/group/assign` | regex 일괄 그룹 지정 | `file_store.assign_group_regex(db, regex, group_name, require_same_fields)` |
| | `/api/group/detail` | 그룹 상세 (필드 합집합 + 기준 파일) | `file_store.get_group_detail(db, group)` |
| | `/api/group/base` | 그룹 기준 파일 지정 | `file_store.set_group_base(db, group_name, base_file)` |
| | `/api/groups/merge-preview` | 합치기 미리보기 (필드 비교) | `file_store.merge_preview(db, from_group, to_group)` |
| | `/api/groups/merge` | 그룹 합치기 (기준 파일 지정) | `file_store.merge_groups(db, from_group, to_group, base_file)` |
| `main.py` (직접) | `/api/health` | 헬스 체크 | `db.get_db().path` |

## 5. UI 레이어 (apps/ui.py)

`GET /`는 순수 HTML/JS 단일 페이지(`templates/index.html`)를 반환하며,
화면의 버튼이 위 API들을 호출합니다. UI는 **백엔드 엔드포인트의 클라이언트**이고
실제 도메인 로직은 포함하지 않습니다.

| UI 동작(버튼) | 호출 방식 | 호출 대상 엔드포인트 |
| --- | --- | --- |
| 스캔 | `call('POST')` | `POST /api/scan` |
| 전체 스캔·변경기록 | `call('POST')` | `POST /api/scan_all` |
| 파일 목록/필터 | `call()` | `GET /api/files?group=` |
| 파일 상세 (행 확장) | `call()` | `GET /api/file/detail?file_id=` |
| 상세 갱신 | `call('POST')` | `POST /api/file/refresh?file_id=` |
| 상세 저장 후 닫기 | `call('POST') + JSON body` | `POST /api/file/save` |
| 그룹 상세 | `call()` | `GET /api/group/detail?group=` |
| 그룹 기준 파일 변경 | `call('POST')` | `POST /api/group/base` |
| 그룹 이름 변경 | `call('POST')` | `POST /api/group/rename` |
| Regex 일괄 지정 | `call('POST')` | `POST /api/group/assign` |
| 그룹 합치기 (미리보기/적용) | `call()` / `call('POST')` | `GET /api/groups/merge-preview`, `POST /api/groups/merge` |

## 6. 핵심 데이터 타입

`lib/file_store.py`의 결과 타입:

| 타입 | 의미 |
| --- | --- |
| `ScanSummary(created, updated, skipped)` | `/api/scan` 결과 |
| `ScanAllSummary(created, updated, parse_failed, details_recorded, details_changed, changes)` | `/api/scan_all` 결과 |
| `FileChange(id, changes)` | 파일별 변경 항목 목록 |

파일 식별: `id = subdir/파일명` (예: `UPF/UPF_XX_payload.csv`), sqlite PK.
조사되지 않은 값은 UI에서 `N/A` 로 표기한다.

## 7. 공통 호출 패턴

거의 모든 핸들러가 다음 2단계로 시작한다:

```python
db     = get_db()          # lib/db.py (런타임 싱글턴, config reload 시 재접속)
config = get_config()      # lib/runtime_config.py (없으면 load_config()로 초기화 후 캐시)
```

- `get_db()` → `lib/db.py` (싱글턴; `reload_config()` 시 다른 `db_path`로 재접속)
- `get_config()` → `lib/runtime_config.py`
- lib 도메인 함수는 `KeyError`(존재하지 않음 → 404), `ValueError`(잘못된 인자 → 400)를
  던지며 `lib/common.install_exception_handlers()`가 HTTP 상태碼로 변환한다.

## 8. 모듈 의존 관계 (요약)

```
main.py               ─► config_loader, runtime_config, db, common,
                         apps/{ui, configuration, scan, files, groups}
apps/ui.py            ─► runtime_config
apps/configuration.py ─► runtime_config, common
apps/scan.py          ─► file_store, db
apps/files.py         ─► file_store, runtime_config, common, db
apps/groups.py        ─► file_store, common, db

lib/file_store.py     ─► scanner, db, runtime_config
lib/runtime_config.py ─► config_loader
lib/config_loader.py  ─► (yaml)
lib/db.py             ─► (aiosqlite)
lib/scanner.py        ─► (csv/json/xml)
lib/common.py         ─► (fastapi)
```

## 9. DB 스키마

```sql
files(id PK, first_seen, last_modified, file_type, group_name, parser, updated_at)
file_fields(id PK, file_id, field_name, keep, rename_to, is_tag, UNIQUE(file_id, field_name))
file_meta(file_id PK, total_records, detail_checked_at)
groups(name PK, base_file, updated_at)   -- 그룹 기준 파일 (추가 테이블)
```
