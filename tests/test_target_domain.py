"""Fictional, de-identified examples for the read-only target-domain gate."""

import pytest

from scout_agent.models.scout import Scout
from scout_agent.target_domain import assess_target_domain


@pytest.mark.parametrize("title,jd,status,category", [
    ("Cloud Engineer", "AWS 基盤の設計・構築を主担当。", "TARGET_CONFIRMED", "CLOUD"),
    ("SRE", "jobContentOutline: SRE として SLO の改善と監視基盤の自動化を担当。",
     "TARGET_CONFIRMED", "SRE"),
    ("ITエンジニア", "jobContentOutline: SRE として SLO の改善と監視基盤の自動化を主担当。",
     "TARGET_CONFIRMED", "SRE"),
    ("DevOps Engineer", "jobContentOutline: CI/CD 基盤の構築とデプロイ自動化を担当。",
     "TARGET_CONFIRMED", "DEVOPS"),
    ("Platform Engineer", "jobContentOutline: 開発組織の生産性向上を担当。\n"
     "jobContentDetail: Kubernetes 基盤の最適化と CI/CD Workflow の設計・提供。",
     "TARGET_CONFIRMED", "PLATFORM"),
    ("Infrastructure Engineer", "Terraform によるインフラ構築と運用自動化を担当。",
     "TARGET_CONFIRMED", "MODERN_INFRA"),
    ("インフラエンジニア", "クラウド案件約半数。希望によりインフラ配属。",
     "TARGET_UNCERTAIN", "CLOUD"),
    ("Cloud Engineer", "AWS案件に挑戦可能。実際の配属先は入社後に決定。",
     "TARGET_UNCERTAIN", "CLOUD"),
    ("ITエンジニア", "Web開発からインフラまで幅広く、スキルに応じて配属。",
     "TARGET_REJECTED", "NONE"),
    ("Oracle EBS基盤", "jobContentOutline: Oracle EBS の保守を担当。\n"
     "jobContentDetail: OCI 移行も一部担当。", "TARGET_UNCERTAIN", "CLOUD"),
    ("クラウドシステムエンジニア", "jobContentOutline: クラウドシステムの障害一次切り分けと"
     "ベンダー調整、OS 更新を担当。", "TARGET_UNCERTAIN", "CLOUD"),
    ("サーバーエンジニア", "jobContentOutline: オンプレサーバの保守と監視を担当。",
     "TARGET_REJECTED", "NONE"),
    ("ネットワークエンジニア", "jobContentOutline: LAN スイッチとルータを設計・運用。",
     "TARGET_REJECTED", "NONE"),
    ("社内SE", "jobContentOutline: 社内PCとアカウント管理、ヘルプデスクを担当。",
     "TARGET_REJECTED", "NONE"),
    ("Web Engineer", "jobContentOutline: AWS 上で稼働する SaaS アプリを開発。",
     "TARGET_REJECTED", "NONE"),
    ("ITエンジニア", "jobContentOutline: 業務システムを開発。\n"
     "targetMemberDetail: AWS と Terraform の経験を歓迎。", "TARGET_REJECTED", "NONE"),
])
def test_target_domain_requires_main_duty_evidence(title, jd, status, category):
    result = assess_target_domain(Scout(platform="mynavi", job_title=title, jd_text=jd))
    assert (result.status, result.category) == (status, category)


def test_target_domain_multi_job_accepts_distinct_cloud_infra_duties():
    scout = Scout(
        platform="type", job_title="自社開発エンジニア（架空） / インフラエンジニア（架空）",
        jd_text="自社開発エンジニア（架空）\nWeb アプリを開発。\n"
                "インフラエンジニア（架空）\nクラウド移行や IaC による運用自動化、"
                "Azure クラウド基盤構築を主担当。",
    )
    result = assess_target_domain(scout)
    assert (result.status, result.category) == ("TARGET_CONFIRMED", "CLOUD")


def test_target_domain_missing_jd_is_uncertain():
    result = assess_target_domain(Scout(platform="green", job_title="Cloud Engineer"))
    assert result.status == "TARGET_UNCERTAIN"


@pytest.mark.parametrize("title,jd,status,category", [
    (
        "グループ向けインフラエンジニア｜M365・端末管理・ネットワーク",
        "仕事内容\nクライアント端末、Microsoft 365、社内ネットワークの"
        "設計・標準化・運用を担当。クラウド時代に適したセキュリティアーキテクチャへ移行。",
        "TARGET_REJECTED", "NONE",
    ),
    (
        "サーバーエンジニア",
        "仕事内容\nWindows/Linuxサーバー設計構築、仮想化基盤、クラウド移行など幅広い"
        "プロジェクト。ご経験や志向に応じて配属。AWS/Azureを活用した環境構築や"
        "Terraformによる構成管理も担当する場合があります。",
        "TARGET_UNCERTAIN", "CLOUD",
    ),
    (
        "バックエンドエンジニア",
        "jobContentOutline: Rubyによる新機能開発を主担当。AWSリソース構築と"
        "CI/CD環境の運用にも寄与。",
        "TARGET_REJECTED", "NONE",
    ),
    (
        "ITエンジニア",
        "jobContentOutline: まずは運用保守・監視業務を中心に担当。"
        "希望者は研修後にクラウド領域へ挑戦可能。",
        "TARGET_REJECTED", "NONE",
    ),
    (
        "ITエンジニア",
        "jobContentOutline: 希望を伺い案件を紹介。\n【プロジェクト事例】\n"
        "＜インフラ・DevOps＞TerraformによるIaC構築、Kubernetes設計。\n"
        "＜開発＞Webアプリ開発。",
        "TARGET_UNCERTAIN", "DEVOPS",
    ),
    (
        "インフラエンジニア",
        "jobContentOutline: AWS/Azureのクラウド基盤設計・構築を主担当。"
        "Terraformでインフラを自動化。",
        "TARGET_CONFIRMED", "CLOUD",
    ),
    (
        "クラウド・サーバーエンジニア",
        "仕事内容\n希望する案件を選べます。具体的な配属は未定。\n"
        "《案件例》\nAWS基盤の設計構築。\nAzure移行。",
        "TARGET_UNCERTAIN", "CLOUD",
    ),
    (
        "インフラエンジニア",
        "仕事内容\n【入社直後の業務】\n・監視スクリプト修正\n"
        "・障害復旧テスト\n・アラート検知時の一次対応\n"
        "・クラウド／ネットワーク設計・構築\n・手順書作成。",
        "TARGET_UNCERTAIN", "CLOUD",
    ),
])
def test_target_domain_rejects_incidental_or_unassigned_cloud_work(title, jd, status, category):
    result = assess_target_domain(Scout(platform="green", job_title=title, jd_text=jd))
    assert (result.status, result.category) == (status, category)
