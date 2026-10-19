"""Shared duty evidence and explicit client-site conflicts; no I/O."""
import re

DEVELOPMENT = re.compile(r'backend|back-end|full.?stack|server.?side|バックエンド|フルスタック|サーバー?サイド|フロントエンド|アプリ|Web.*開発|システム.*開発|(?:サービス|製品|プロダクト|クラウド|cloud.native).{0,25}(?:開発|実装|develop)', re.I)
NONCLOUD = re.compile(r'非クラウド|クラウド(?:は|を)?(?:使用しない|利用しない)|オンプレ(?:ミス)?のみ|non.cloud', re.I)
CLOUD_SERVICE = r'AWS|Azure|GCP|Lambda|API Gateway|DynamoDB|ECS|EKS|クラウド|cloud'
CLOUD_APP_DUTY = re.compile(
    rf'(?:{CLOUD_SERVICE})[^。\n]{{0,65}}(?:開発|実装|デプロイ|deploy|develop|implement)|'
    rf'(?:開発|実装|デプロイ|deploy|develop|implement)[^。\n]{{0,65}}(?:{CLOUD_SERVICE})', re.I)
INHOUSE = re.compile(r'自社(?:サービス|製品|プロダクト|開発|内|オフィス)|内製|受託.*(?:自社|社内)|in.house', re.I)


# Product ownership is separate from this hire's workplace/assignment.
INHOUSE_WORKPLACE = re.compile(
    r'自社(?:内|オフィス|事務所|拠点)(?:で|に|勤務|作業)|自社勤務|社内勤務|'
    r'非派遣|派遣では(?:ない|ありません)|'
    r'(?:客先|顧客先|クライアント先)常駐(?:は|が)?(?:一切)?(?:なし|無し|ありません|しない|ではない|ゼロ|のない)', re.I)


def confirmed_inhouse_workplace(text: str) -> bool:
    return bool(INHOUSE_WORKPLACE.search(text)) and not re.search(
        r"(?:配属|勤務地|担当業務).{0,20}(?:未定|入社後|決定|不明)|"
        r"(?:希望|適性|スキル|経験).{0,30}(?:配属|案件)|いずれかに配属|案件もある", text,
    )


def client_site_conflict(text: str) -> bool:
    # Sentence/clause scope prevents a company history or a negation from
    # either creating a conflict or hiding a later explicit role assignment.
    for clause in re.split(r'[。\n；;]|[、,](?=本募集|本職|本ポジション|当ポジション)|(?=本募集|本職|本ポジション|当ポジション)', text):
        clause = re.sub(r'(?:客先|顧客先|クライアント先)常駐(?:は|が)?(?:一切)?(?:なし|無し|ありません|しない|ではない|ゼロ|のない)|SES\s*(?:なし|ではない|ではありません)', '', clause, flags=re.I)
        # Only business descriptions, not a concrete hire/assignment, are exempt.
        if (re.search(r'(?:当社|弊社|会社|企業|会社紹介|沿革).*(?:SES|常駐).*(?:事業|運営|展開)|(?:SES|常駐).*事業も|(?:過去|以前).*(?:SES|常駐)', clause, re.I)
                and not re.search(r'本募集|本職|本ポジション|当ポジション|配属|勤務|担当', clause)):
            continue
        if re.search(r'(?:客先|顧客先|クライアント先)常駐|派遣先(?:勤務|で勤務|に勤務)|SES\s*案件(?:に|へ)?配属|(?:案件|プロジェクト).{0,15}客先.{0,12}(?:勤務|常駐)|SES事業で顧客プロジェクトに配属|案件選択制で客先|案件配属制で顧客|客户项目配属为主', clause, re.I):
            return True
    return False


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
