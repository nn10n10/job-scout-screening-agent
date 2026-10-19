"""Shared duty evidence and explicit client-site conflicts; no I/O."""
import re

DEVELOPMENT = re.compile(r'backend|back-end|full.?stack|server.?side|バックエンド|フルスタック|サーバー?サイド|フロントエンド|アプリ|Web.*開発|システム.*開発|(?:サービス|製品|プロダクト|クラウド|cloud.native).{0,25}(?:開発|実装|develop)', re.I)
NONCLOUD = re.compile(r'非クラウド|クラウド(?:は|を)?(?:使用しない|利用しない)|オンプレ(?:ミス)?のみ|non.cloud', re.I)
CLOUD_SERVICE = r'AWS|Azure|GCP|Lambda|API Gateway|DynamoDB|ECS|EKS|クラウド|cloud'
CLOUD_APP_DUTY = re.compile(
    rf'(?:{CLOUD_SERVICE})[^。\n]{{0,65}}(?:開発|実装|デプロイ|deploy|develop|implement)|'
    rf'(?:開発|実装|デプロイ|deploy|develop|implement)[^。\n]{{0,65}}(?:{CLOUD_SERVICE})', re.I)
INHOUSE = re.compile(r'自社(?:サービス|製品|プロダクト|開発|内|オフィス)|内製|受託.*(?:自社|社内)|in.house', re.I)


def client_site_conflict(text: str) -> bool:
    # Remove individual negative statements, never suppress a separate conflict.
    text = re.sub(r'(?:客先|顧客先|クライアント先)常駐(?:は|が)?(?:一切)?(?:なし|無し|ありません|しない|ではない|ゼロ)|SES\s*(?:なし|ではない|ではありません)', '', text, flags=re.I)
    # A company's SES business alone says nothing about this assignment.
    return bool(re.search(r'(?:客先|顧客先|クライアント先)常駐|派遣先(?:勤務|で勤務|に勤務)|SES\s*案件(?:に|へ)?配属|(?:案件|プロジェクト).{0,15}客先.{0,12}(?:勤務|常駐)|SES事業で顧客プロジェクトに配属|案件選択制で客先|案件配属制で顧客|客户项目配属为主', text, re.I))


def development_candidate(title: str, duties: str) -> bool:
    return bool(DEVELOPMENT.search(title + '\n' + duties))


def preserve_development_candidate(title: str, duties: str) -> bool:
    """Cloud mention is enough to defer a development role to classification.

    It is never enough to confirm it. Clearly non-cloud duties retain existing
    exclusions; missing duties cannot establish a non-target assignment.
    """
    if re.search(r"スマホアプリ|モバイルアプリ|Android|iOS|組込|組み込み", title + duties, re.I):
        return False
    return development_candidate(title, duties) and (
        bool(re.search(CLOUD_SERVICE, title + "\n" + duties, re.I)) or not re.search(r"開発|実装|設計|構築|運用|保守|監視|担当|develop|implement|design|build", duties, re.I)
    )
