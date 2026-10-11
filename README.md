# 📁 파일 스캐너 (FastAPI)

Python + FastAPI 기반 파일 스캐너 웹 애플리케이션.

`parser-config.yaml` 의 `files.initialdir` 경로를 스캔하여 파일을 sqlite에 기록하고,
파일별 타입/필드/그룹을 관리하는 Web UI 를 제공합니다.

## 실행

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

브라우저: <http://localhost:8000/>

설정 파일을 다른 위치에 두려면 `PARSER_CONFIG=/path/to/parser-config.yaml` 환경변수를 사용하세요.

## 기동 구성 (`parser-config.yaml`)

```yaml
files:
  initialdir: data        # 스캔할 루트 (상대경로 → 설정파일 기준 절대경로)
  db_path: scan.db        # sqlite 저장 위치
parsers:
  - name: json_to_records   # name 항목들이 파일 상세보기의 파서 목록으로 노출
    description: "..."
```

## 동작

### 스캔하기
- `files.initialdir` 을 재귀 스캔.
- 각 파일의 **최초 발견 시점**(ctime)과 **마지막 변경일**(mtime)을 `files` 테이블에 저장.
- 이미 존재하면 `last_modified` 만 갱신 (first_seen / group_name / parser 등 기존 설정 보존).
- ID = `subdir/파일명` (예: `UPF/UPF_XX_payload.csv`, slash 구분).

### 파일 상세보기 (행을 클릭)
- 최초 진입 시 실제 파일을 읽어 **타입(csv/json/xml/txt)**, **필드 목록**, **레코드 수**를 DB에 기록.
  - 타입은 내용 기반으로 자동 판정 (json→xml→csv 순, 불가 시 `txt`).
  - 이후에는 **갱신하기** 버튼을 누를 때만 재읽기.
- 필드별로 **유지(keep)** 체크, **이름 변경(rename_to)**, **태그 지정(is_tag)** 가능.
- 유지 체크된 필드 중 태그 지정된 것은 목록의 "태그" 열에 표시됩니다.
- **파서**를 설정의 `parsers[].name` 목록에서 선택 가능.
- **저장 후 닫기** 시 변경된 항목만 DB에 반영하고 확장 영역을 제거.
- 조사되지 않은 값은 `N/A` 로 표기.

### 파일보기 (그룹 중심 목록)
- 열: 파일명(경로 포함) / 그룹 / 최초발견 / 마지막 변경 / 타입 / 필드수 / 유지 필드수 / 태그.
- 그룹명 필터 입력 시 **실시간**으로 포함되는 그룹만 표시.

### 그룹 기능
- 그룹명 기본값 = 파일 ID, 자유롭게 변경 가능.
  - **이름 변경**: 드롭다운에서 그룹 선택 → 새 이름 입력 (해당 그룹의 모든 파일 일괄 변경).
- **Regex 기반 일괄 지정**: 정규식으로 매칭된 파일들의 그룹명을 한꺼번에 지정.
  - "동일한 필드를 가진 파일만" 체크 시, 필드 구성이 같은 파일만 지정 대상.

## DB 스키마

- `files(id PK, first_seen, last_modified, file_type, group_name, parser, updated_at)`
- `file_fields(id PK, file_id, field_name, keep, rename_to, is_tag, UNIQUE(file_id, field_name))`
- `file_meta(file_id PK, total_records, detail_checked_at)`

## REST API

| Method | Path | 설명 |
|--------|------|------|
| GET  | `/` | 웹 UI |
| POST | `/api/scan` | initialdir 스캔 |
| GET  | `/api/files?group=` | 파일 목록 (그룹 필터) |
| GET  | `/api/groups` | 그룹 목록 |
| GET  | `/api/parsers` | 파서 이름 목록 |
| GET  | `/api/file/detail?file_id=` | 파일 상세 (최초 진입 시 DB 기록) |
| POST | `/api/file/refresh?file_id=` | 파일 재읽기/갱신 (설정 보존) |
| POST | `/api/file/save` | 상세 변경 저장 |
| POST | `/api/group/rename` | 그룹명 변경 |
| POST | `/api/group/assign` | regex 기반 일괄 그룹 지정 |
| GET  | `/api/health` | 헬스 체크 |
