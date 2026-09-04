#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
NewFace Monitor System (Refactored for MY_HOME_SYSTEM)
Targets: MonitorConfig.SITES に登録された複数サイト

Description:
    複数のWebサイトの新人紹介ページを定期巡回し、新規キャストの追加を検知してDiscordに通知する。
    監視対象サイトは MonitorConfig.SITES に SiteConfig を追加するだけで拡張できる。
    MY_HOME_SYSTEMのエコシステムに統合されたバージョン。
"""

import os
import json
import re
import time
import random
import sys
import logging
import hashlib
import fcntl
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Set, Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse, parse_qs

from file_utils import DiscordCircuitBreaker

# プロジェクトルート（DDDの親ディレクトリ）をパスに追加
CURRENT_DIR = Path(__file__).resolve().parent # ~/develop/DDD
PROJECT_ROOT = CURRENT_DIR.parent / "MY_HOME_SYSTEM" # ~/develop/MY_HOME_SYSTEM
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup, NavigableString

# MY_HOME_SYSTEM Core Imports
try:
    # システム統合環境下でのインポート
    from core.logger import get_logger
    from core.nas_utils import get_managed_target_directory
    from core.utils import wait_for_storage_warmup
except ImportError:
    # 単体テスト用・モジュール欠損時のフォールバック
    import logging
    import time
    from pathlib import Path

    logging.basicConfig(level=logging.INFO)
    def get_logger(name: str) -> logging.Logger: 
        return logging.getLogger(name)
        
    def get_managed_target_directory(*args, **kwargs) -> Path:
        # 呼び出し元(get_data_dir)はfallback_dir_str（BASE_DIR/'data'の絶対パス）を
        # 渡してくる想定。これを無視してカレントディレクトリ相対の"./data"を返すと、
        # 実行時のカレントディレクトリ次第で保存先が毎回変わってしまい、
        # known_casts_*.jsonが見つからず全キャストを新人として誤検知する原因になる。
        fallback_dir_str = kwargs.get("fallback_dir_str")
        if fallback_dir_str:
            return Path(fallback_dir_str)
        return Path("./data")

    def wait_for_storage_warmup(target_dir: Path, max_retries: int = 5, base_delay: float = 1.0) -> bool:
        """
        NAS等のストレージがマウントされ、書き込み可能になるまで待機する。
        Exponential Backoffを用いてリトライを行い、テストファイルの作成・削除で死活確認を行う。

        Args:
            target_dir (Path): アクセス確認を行う対象ディレクトリ。
            max_retries (int): 最大リトライ回数。
            base_delay (float): ベースとなる待機時間（秒）。

        Returns:
            bool: ストレージへのアクセスが確立できた場合はTrue、タイムアウトした場合はFalse。
        """
        logger = get_logger("storage_warmup")
        
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.debug(f"Directory creation failed for {target_dir}: {e}")
            # 作成失敗時も後続のI/Oテストで確実なハンドリングを行うため処理を継続

        test_file = target_dir / ".storage_warmup_test"
        
        for attempt in range(max_retries):
            try:
                # テストファイルの書き込みと削除で物理的なI/O確認
                test_file.write_text("warmup_check", encoding="utf-8")
                test_file.unlink()
                logger.debug(f"Storage warmup verified at {target_dir}")
                return True
            except (IOError, OSError) as e:
                delay = base_delay * (2 ** attempt)
                logger.debug(f"Storage access failed (Attempt {attempt + 1}/{max_retries}): {e}. Retrying in {delay}s...")
                time.sleep(delay)

        # 最終的にアクセスできない場合はパニックを起こさずFalseを返す
        logger.error(f"Storage warmup failed after {max_retries} attempts.")
        return False

# ==========================================
# Logger Initialization
# ==========================================
logger = get_logger("newface_monitor")

# ==========================================
# Configuration & Constants
# ==========================================

# 名前要素のテキストから年齢を抽出するための正規表現。
# "うるは(23歳)" / "浅見ゆき（30）" / "小鳥(ことり)セラピスト  22歳" のように、
# 括弧付き数字(全角/半角どちらも)、または「歳」「才」が数字に続く形式の
# いずれかを年齢表記とみなす。実在の年齢は2桁のため誤検知を避けるため
# 桁数を2桁に限定する(例: ランキングバッジ等の"(1)"のような1桁の
# 括弧数字を誤って年齢と判定しないようにする)。
AGE_PATTERN = re.compile(r'[（(]\s*(\d{2})\s*(?:歳|才)?\s*[）)]|(\d{2})\s*(?:歳|才)')


@dataclass(frozen=True)
class SiteConfig:
    """監視対象サイト1件分の設定。

    新しいサイトを監視対象に加える場合は、このデータクラスのインスタンスを
    MonitorConfig.SITES に追加するだけでよい（コード本体の変更は不要）。

    Attributes:
        site_id (str): サイトを一意に識別するID（データファイル名等に使用）。
        name (str): 通知等に表示するサイトの表示名。
        target_url (str): 巡回対象ページのURL。
        selector_container (str): キャスト一覧の各要素を囲むコンテナのCSSセレクタ。
        selector_name (str): キャスト名を取得するCSSセレクタ（コンテナ基準）。
        selector_link (str): 詳細ページへのリンクを取得するCSSセレクタ（コンテナ基準）。
        selector_image (str): サムネイル画像を取得するCSSセレクタ（コンテナ基準）。
        data_filename (str): 既知キャストの保存先ファイル名。未指定時は
            'known_casts_{site_id}.json' を用いる。
        id_query_param (Optional[str]): 詳細ページURLがクエリパラメータで
            キャストを識別する形式（例: 'profile.php?id=931'）のサイト向け。
            指定した場合、そのクエリパラメータの値をキャストIDとして使用する
            （未指定時はURLパスの末尾セグメントからIDを生成する従来ロジックを使う）。
            なお、'profile.html?12199' のようにキー=値ではなくクエリ文字列自体が
            IDを表すサイトについては、id_query_param未設定でも自動的に
            クエリ文字列全体（'='を含まない場合）をIDとして採用する。
        image_attr (str): サムネイル画像URLを取得する際に、selector_imageで
            マッチした要素から読み取る属性名。lazyload実装のサイトでは実URLが
            'src'ではなく'data-original'等に入っていることがあるため指定する
            （image_from_style=Trueの場合は無視される）。
        image_from_style (bool): Trueの場合、selector_imageでマッチした要素の
            'style'属性から "background-image:url(...)" 形式で画像URLを抽出する。
            サムネイルが<img src>ではなく要素のインラインCSSで指定されている
            サイト向け（この場合 image_attr は使用しない）。
        name_first_text_only (bool): Trueの場合、selector_nameでマッチした要素
            直下のテキストノードのうち、前後の空白を除いて最初に空でなくなる
            ものを名前として使用する（子要素・空白のみのテキストノードは
            読み飛ばす）。名前の要素内に年齢バッジ等が兄弟の子要素として
            同居しており、get_text()では "さな(27)" のように汚染されて
            しまうサイトや、"<small>Name</small>実際の名前" のように
            ラベル用の子要素の後ろに実テキストが続くサイト向け
            （未指定時は要素全体のget_text()を使う従来ロジック）。
        name_strip_after_tab (bool): Trueの場合、名前取得後にタブ文字(\t)で
            分割し先頭部分のみを採用する。年齢等の付加情報が兄弟要素ではなく
            同一テキストノード内にタブ区切りで同居しているサイト向け
            （例: "芹沢\t\t\t(40歳)"）。全角スペース区切りの姓名（例:
            "神谷　しおり"）は対象外のため、空白ではなくタブのみで判定する。
        skip_unnamed_casts (bool): Trueの場合、名前が取得できなかったカード
            （selector_name不一致・テキスト空の両方）を通知・登録の対象から
            除外する。一覧に名前空・身長0cm等のプレースホルダーカードが
            混ざるサイト向け（例: yui_mrsteiのprofile?id=81）。未指定時は
            従来どおり'Unknown'として扱う。
    """
    site_id: str
    name: str
    target_url: str
    selector_container: str
    selector_name: str
    selector_link: str
    selector_image: str
    data_filename: str = ""
    id_query_param: Optional[str] = None
    image_attr: str = "src"
    image_from_style: bool = False
    name_first_text_only: bool = False
    name_strip_after_tab: bool = False
    skip_unnamed_casts: bool = False

    def get_data_filename(self) -> str:
        """既知キャストの保存先ファイル名を返す。

        Returns:
            str: data_filename が指定されていればそれを、なければ
                site_id から導出したデフォルトファイル名を返す。
        """
        return self.data_filename or f"known_casts_{self.site_id}.json"


class MonitorConfig:
    """モニタリング設定および定数管理クラス。"""

    # Target Sites
    # 新規サイトを監視対象に追加する場合は、このリストに SiteConfig を追記する。
    SITES: List[SiteConfig] = [
        SiteConfig(
            site_id='petitpetit_dream',
            name='ぷちぷちどりーむ',
            target_url='https://petitpetit-dream.com/newface/',
            selector_container='ul.gallist li',
            selector_name='article h3 a',
            selector_link='article h3 a',
            selector_image='div.ph img:not(.list_today)',
            # 既存運用データ（known_casts.json）との後方互換のためファイル名を明示指定
            data_filename='known_casts.json',
        ),
        SiteConfig(
            site_id='merci_spa',
            name='Mrs.Merci SPA',
            target_url='https://merci-spa.com/girllist',
            selector_container='div.therapist_list > div',
            selector_name='h3.therapist_name',
            selector_link='a.therapist_meta',
            selector_image='p.therapist_img img',
        ),
        SiteConfig(
            site_id='osaka_milktea',
            name='ミルクティー -milktea-',
            target_url='https://osakamilktea.com/sp/newface.php',
            selector_container='main#newface section > ul',
            selector_name='div.name',
            # div.photo 内にSNSアイコンへのリンク/画像も混在しているため、
            # プロフィールページへのリンクをhrefで明示的に絞り込む
            selector_link='a[href*="profile.php"]',
            selector_image='a[href*="profile.php"] img',
            # 詳細URLが './profile.php?id=931' のようにクエリパラメータで
            # キャストを識別する形式のため、'id'パラメータの値をIDとして使う
            id_query_param='id',
        ),
        SiteConfig(
            site_id='itadaki',
            name='ITADAKI（イタダキ）',
            target_url='https://www.itadaki-top.com/staff/',
            selector_container='div#companion ul li',
            selector_name='div.txt a',
            selector_link='div.txt a',
            selector_image='div.img img',
            # 画像がlazyload実装のため、実URLは src ではなく data-original に入っている
            image_attr='data-original',
        ),
        SiteConfig(
            site_id='cecile',
            name='Cecile セシル',
            target_url='https://cecile.men-este.com/therapist.html',
            # レイアウト調整用の空divが混在するため、リンクを持つ要素のみに絞る
            selector_container='div.staff-box:has(a)',
            selector_name='li.staff-name',
            selector_link='div.staff-image a',
            selector_image='div.staff-image a',
            # サムネイルが<img src>ではなく<a>のインラインCSS背景画像で指定されている
            image_from_style=True,
            # 詳細URLが './profile.html?sid=83' 形式のため 'sid'パラメータをIDとして使う
            id_query_param='sid',
        ),
        SiteConfig(
            site_id='restpia',
            name='Restpia レストピア',
            target_url='https://restpia-es.com/therapist.html',
            # レイアウト調整用の空divが混在するため、リンクを持つ要素のみに絞る
            selector_container='div.staff-box:has(a)',
            # box-inner内の最初のliが名前（class指定がないため先頭要素で取得）
            selector_name='ul.box-inner li',
            selector_link='div.staff-image01 a',
            selector_image='div.staff-image01 a',
            image_from_style=True,
            id_query_param='sid',
        ),
        SiteConfig(
            site_id='anju_spa',
            name='Anju spa（アンジュスパ）',
            target_url='https://anjuspa.com/cast/',
            selector_container='ul.gallist li',
            # petitpetit-dreamと異なり、名前が<a>で囲まれていないためarticle h3のみで取得
            selector_name='article h3',
            selector_link='a[href*="/profile?id="]',
            selector_image='div.ph img',
            # 詳細URLが '/profile?id=191' 形式のため 'id'パラメータをIDとして使う
            id_query_param='id',
        ),
        SiteConfig(
            site_id='sr_himawari',
            name='シークレットルームヒマワリ',
            target_url='https://sr-himawari.com/cast/',
            selector_container='ul.gallist li',
            selector_name='article h3',
            selector_link='a[href*="/profile?id="]',
            selector_image='div.ph img',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='aura',
            name='AURA（オーラ）',
            target_url='https://aura-takatsuki.com/itemList.html',
            selector_container='li.itemData',
            selector_name='a.itemName',
            selector_link='a.itemName',
            # ホバー用の2枚目画像(layerHover)より先に通常表示用の画像がDOM順で
            # 出現するため、select_one（最初の一致）で正しく通常画像を取得できる
            selector_image='a.imageLink img.castImage',
        ),
        SiteConfig(
            site_id='viaura',
            name='ミセス美オーラ',
            target_url='https://mrs-viaura.com/model.html',
            # 各キャストを囲むdivにはclass指定が無いため、
            # プロフィールへのリンクを直接の子に持つdivをコンテナとする
            selector_container='div:has(> a[href^="model_profile"])',
            selector_name='h3.model_name',
            selector_link='a[href^="model_profile"]',
            # div.model_image内の最初のimgはランクアイコンのため、
            # 直接の子要素のimgのみに絞って実際の写真を取得する
            # （写真未登録のキャストは "no_image.webp" プレースホルダーになる）
            selector_image='div.model_image > img',
            # このサイトはShift-JISでエンコードされているが、
            # BeautifulSoupがmetaタグから自動判定するためコード側の対応は不要
        ),
        SiteConfig(
            site_id='kitty',
            name='kitty',
            target_url='https://spa-kt.com/girl',
            selector_container='div.c-panel',
            selector_name='p.c-panel__name',
            selector_link='div.c-panel__image a',
            selector_image='div.c-panel__image img',
            # 詳細URLが 'profile?lid=23' 形式のため 'lid'パラメータをIDとして使う
            id_query_param='lid',
        ),
        # bellica は2026-09-02にサイト閉鎖を確認したため監視対象から削除
        # (ドメインがホスティング業者のデフォルト自己署名証明書+ポータルサイト
        # www.undernavi.com への302リダイレクトに変化し、コンテンツが消失)
        SiteConfig(
            site_id='osaka_mkd',
            name='もくきん堂',
            target_url='https://www.osaka-mkd.com/newface/',
            selector_container='ul.newface_list li',
            # 同じ<li>内に複数の<p>があり名前用のclass指定が無いため、
            # 出現順（1番目:入店日, 2番目:名前, 3番目:年齢）で位置指定する
            selector_name='p:nth-of-type(2)',
            selector_link='a.list-girl-image',
            # <a>内には実写真imgの後にプレミアムバッジ用imgもあるが、
            # 実写真が先に出現するためselect_one（最初の一致）で問題ない
            selector_image='a.list-girl-image img',
        ),
        SiteConfig(
            site_id='la_bella',
            name='LA BELLA（ラベーラ）',
            target_url='https://labella-osaka.net/newface/',
            selector_container='div.listTypeJ li',
            selector_name='h3',
            selector_link='a[href*="profile?uid="]',
            selector_image='img.myPhoto',
            # 画像がlazyload実装のため、実URLはsrcではなくdata-original属性に入っている
            image_attr='data-original',
            id_query_param='uid',
        ),
        SiteConfig(
            site_id='mrs_komoriuta',
            name='ミセスの子守唄',
            target_url='https://mrs-komoriuta.com/newface/',
            # LA BELLAと同一テンプレート（KNZ系）のため設定内容も同様
            selector_container='div.listTypeJ li',
            selector_name='h3',
            selector_link='a[href*="profile?uid="]',
            selector_image='img.myPhoto',
            image_attr='data-original',
            id_query_param='uid',
        ),
        SiteConfig(
            site_id='milkspa_a',
            name='みるくSPAα（ミルクスパアルファ）',
            target_url='https://milkspa-a.com/newface.html',
            # ul#newface-wrapper の子孫セレクタで単純にliを取ると、各キャスト内の
            # ul.state（出勤アイコン等）配下のliまで拾ってしまうため、
            # 直接の子要素(>)のみをコンテナとする
            selector_container='ul#newface-wrapper > li',
            selector_name='p.name',
            selector_link='div.thum a',
            selector_image='div.thum a img',
        ),
        SiteConfig(
            site_id='yui_mrstei',
            name='結-YUI-ミセス邸',
            target_url='https://yui-mrstei.com/cast/',
            # Anju spa / シークレットルームヒマワリと同一テンプレート系列
            selector_container='ul.gallist li',
            selector_name='article h3',
            selector_link='a[href*="/profile?id="]',
            selector_image='div.ph img',
            id_query_param='id',
            # 一覧末尾に名前空・身長0cmのプレースホルダーカード
            # (例: profile?id=81) が混ざるため、名前が取れないカードは除外する
            skip_unnamed_casts=True,
        ),
        SiteConfig(
            site_id='aube_spa',
            name='Mrs.AUBE SPA（オーブスパ）',
            target_url='https://aube-spa.com/girllist',
            # Mrs.Merci SPA / Restpiaと同一テンプレート系列
            selector_container='div.therapist_list > div',
            selector_name='h3.therapist_name',
            selector_link='a.therapist_meta',
            selector_image='p.therapist_img img',
        ),
        SiteConfig(
            site_id='mrs_luxe',
            name='Mrs.Luxe（ミセスリュクス）',
            target_url='https://luxe-mrs.com/lady.php',
            selector_container='ul.ladyList li',
            selector_name='h2.ladyName',
            selector_link='a[href*="lady_num="]',
            selector_image='span.thumb img',
            id_query_param='lady_num',
        ),
        SiteConfig(
            site_id='mrs_onlyone',
            name='Mrs.OnlyOne（ミセスオンリーワン）',
            target_url='https://mrs-onlyone.com/girllist',
            # Mrs.Merci SPA / Restpia / Mrs.AUBE SPAと同一テンプレート系列
            selector_container='div.therapist_list > div',
            selector_name='h3.therapist_name',
            selector_link='a.therapist_meta',
            selector_image='p.therapist_img img',
        ),
        SiteConfig(
            site_id='tropical_spa',
            name='トロピカルSPA',
            target_url='https://tropical-osaka.com/news.php',
            # Bellicaと同一テンプレート系列。新人情報一覧が個別プロフィール
            # ページへのリンクを持たないブログ形式のため、リンクは常に
            # 見つからない（detail_urlは一覧ページURLへ、cast_idは名前ベース
            # へフォールバックする）
            selector_container='ul.newsList li',
            selector_name='h2.title',
            selector_link='a',
            selector_image='span.thumb img',
        ),
        SiteConfig(
            site_id='tenjoubito_premium',
            name='天上人PREMIUM',
            target_url='https://www.tenjoubitopr.com/staff/',
            # 各キャストの<li>等のラッパーが無く、カード自体が<a>要素のため、
            # 子孫に<a>は存在しない（selector_linkは常にマッチしない）。
            # コンテナ自体をリンクとして使うフォールバック(_parse_html側で対応)を利用する
            selector_container='div#staff a.cbox',
            selector_name='p.name',
            selector_link='a',
            selector_image='div.img img',
            # 画像がlazyload実装のため、実URLはsrcではなくdata-original属性に入っている
            image_attr='data-original',
        ),
        SiteConfig(
            site_id='restspa_premium',
            name='REST SPA PREMIUM（レストスパプレミアム）',
            target_url='https://restspa-premium.com/newface/',
            # petitpetit-dreamと同一テンプレート系列だが、article内にタグ一覧の
            # <ul><li>が入れ子であるため、子孫セレクタだとそのliまで拾って
            # しまう。直接の子要素(>)のみをコンテナとする
            selector_container='ul.gallist > li',
            selector_name='article h3 a',
            selector_link='article h3 a',
            selector_image='div.ph img:not(.list_today)',
            # 詳細URLが '/profile?id=220' 形式のため 'id'パラメータをIDとして使う
            id_query_param='id',
        ),
        SiteConfig(
            site_id='firstclass_osaka',
            name='ファーストクラス',
            target_url='https://firstclass-osaka.net/rookie',
            selector_container='div.cast_post',
            selector_name='footer h3',
            selector_link='a.cast_image',
            selector_image='a.cast_image img',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='chaton',
            name='Chaton〜シャトン〜',
            target_url='https://spa-chaton.com/girl',
            # kittyと同一テンプレート系列
            selector_container='div.c-panel',
            selector_name='p.c-panel__name',
            selector_link='div.c-panel__image a',
            selector_image='div.c-panel__image img',
            id_query_param='lid',
        ),
        SiteConfig(
            site_id='yolu_spa',
            name='YOLU SPA Mrs.',
            target_url='https://mrs-yoluspa.net/%e3%82%bb%e3%83%a9%e3%83%94%e3%82%b9%e3%83%88%e4%b8%80%e8%a6%a7/',
            selector_container='div.therapist-item',
            selector_name='a.name-link',
            selector_link='a.name-link',
            selector_image='img.therapist-image',
        ),
        SiteConfig(
            site_id='rose_gentleman',
            name='薔薇と紳士',
            target_url='https://www.rose-gentleman.com/therapist.php',
            selector_container='ul#list li',
            selector_name='div.name',
            selector_link='a[href*="profile.php?id="]',
            selector_image='div.image img',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='royal_thoroughb',
            name='Royal Thoroughb（ロイヤルサラブ）',
            target_url='https://royalthoroughb.com/newface/',
            # LA BELLA / ミセスの子守唄 / DEAR Premiumと同一テンプレート(KNZ系)
            selector_container='div.listTypeJ li',
            selector_name='h3',
            selector_link='a[href*="profile?uid="]',
            selector_image='img.myPhoto',
            image_attr='data-original',
            id_query_param='uid',
        ),
        SiteConfig(
            site_id='dear_premium',
            name='DEAR Premium（ディアープレミアム）',
            target_url='https://dear-premium.net/newface/',
            # LA BELLA / ミセスの子守唄 / Royal Thoroughbと同一テンプレート(KNZ系)
            selector_container='div.listTypeJ li',
            selector_name='h3',
            selector_link='a[href*="profile?uid="]',
            selector_image='img.myPhoto',
            image_attr='data-original',
            id_query_param='uid',
        ),
        SiteConfig(
            site_id='kakurega',
            name='隠れ家',
            target_url='https://kakurega-iyashi.com/girllist',
            # Mrs.Merci SPA / Restpia / Mrs.AUBE SPA / Mrs.OnlyOneと同一テンプレート系列。
            # このサイトはdiv.therapist_list_wrapperという追加のラッパーが挟まるが、
            # selector_name等は子孫探索のため影響を受けない
            selector_container='div.therapist_list > div',
            selector_name='h3.therapist_name',
            selector_link='a.therapist_meta',
            selector_image='p.therapist_img img',
        ),
        SiteConfig(
            site_id='tororintime',
            name='とろりんタイム',
            target_url='https://tororintime.com/newface/',
            # ぷちぷちどりーむと同一テンプレート(gallist/article/h3系)。
            # div.type内に別のul>liのタグ一覧が入れ子になっているため、
            # コンテナは子要素セレクタ(>)で直下のliのみに絞り込む
            selector_container='ul.gallist > li',
            selector_name='article h3 a',
            selector_link='article h3 a',
            selector_image='div.ph img:not(.list_today)',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='osaka_milktea_therapist',
            name='ミルクティー -milktea-（セラピスト一覧）',
            target_url='https://osakamilktea.com/therapist.php',
            # 既存のosaka_milktea(newface.php/新人情報)とは別ページ(therapist.php/在籍一覧)のため、
            # 別サイトとして独立管理する
            selector_container='main#therapist ul > li.box',
            selector_name='div.name',
            selector_link='a[href*="profile.php"]',
            selector_image='a[href*="profile.php"] img',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='mrs_femmefatale',
            name='Mrs.Femme Fatale（ミセスファムファタール）',
            target_url='https://mrs-femmefatale.com/newface/',
            # LA BELLA / ミセスの子守唄 / Royal Thoroughb / DEAR Premiumと同一テンプレート(KNZ系)
            selector_container='div.listTypeJ li',
            selector_name='h3',
            selector_link='a[href*="profile?uid="]',
            selector_image='img.myPhoto',
            image_attr='data-original',
            id_query_param='uid',
        ),
        SiteConfig(
            site_id='yudanete',
            name='ゆだねて',
            target_url='https://www.yudanete.com/therapist.html',
            # サムネイル画像は<img>ではなく、リンク(<a>)自体のstyle属性に
            # background-imageとして埋め込まれている。また、グリッドレイアウト用の
            # 空のdiv.staff-box（余白調整用）が末尾に混在するため:has()で除外する
            selector_container='div.staff-wrap > div.staff-box:has(li.staff-name)',
            selector_name='li.staff-name',
            selector_link='a[href*="profile.html"]',
            selector_image='div.staff-image a',
            image_from_style=True,
            id_query_param='sid',
        ),
        SiteConfig(
            site_id='aromade_skit',
            name='すきっと',
            target_url='https://aromade-skit.com/therapists/',
            selector_container='ul.therapists-boxwrap > li.therapists-box',
            selector_name='h3.name',
            selector_link='a.expand-link',
            selector_image='a.expand-link img',
            id_query_param='author',
        ),
        SiteConfig(
            site_id='aquaspa_osaka',
            name='aqua SPA(アクアスパ)',
            target_url='https://aquaspa-osaka.com/newface/',
            # とろりんタイム/ぷちぷちどりーむと同一テンプレート(gallist/article/h3系)
            selector_container='ul.gallist > li',
            selector_name='article h3 a',
            selector_link='article h3 a',
            selector_image='div.ph img:not(.list_today)',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='genie',
            name='genie(ジーニー)',
            target_url='https://genie-osaka.com/staff.html',
            # 詳細URLが 'profile.html?12199' のようにキー=値ではなくクエリ文字列
            # 自体でIDを表す新規パターン。id_query_paramは指定不要（_parse_html側の
            # 汎用フォールバックでクエリ文字列全体を自動的にIDとして採用する）。
            # サムネイルはfigure直下の<img>のみを対象とし、内側にネストされた
            # NEWバッジ用<img>は子要素セレクタ(>)で除外する
            selector_container='ul.c-list-therapist > li',
            selector_name='div.c-list-therapist__name',
            selector_link='a[href*="profile.html"]',
            selector_image='figure.c-list-therapist__img > img',
        ),
        SiteConfig(
            site_id='amaou_therapi',
            name='ミセスあまおうセラピ',
            target_url='https://amaou-therapi.jp/lady.php',
            selector_container='ul.ladyList > li',
            selector_name='h2.ladyName',
            selector_link='a[href*="lady_detail.php"]',
            selector_image='span.thumb img',
            id_query_param='lady_num',
        ),
        SiteConfig(
            site_id='spamaybe',
            name='spaMAYBE',
            target_url='https://spamaybe.com/girl',
            # kitty/Chatonと同一テンプレート系列(c-panel系)
            selector_container='div.c-panel',
            selector_name='p.c-panel__name',
            selector_link='div.c-panel__image a',
            selector_image='div.c-panel__image img',
            id_query_param='lid',
        ),
        SiteConfig(
            site_id='alyo',
            name='ALYO（アルヨ）',
            target_url='https://alyo.net/newface/',
            # la_bella等と同一テンプレート(KNZ系listTypeJ)
            selector_container='div.listTypeJ li',
            selector_name='h3',
            selector_link='a[href*="profile?uid="]',
            selector_image='img.myPhoto',
            image_attr='data-original',
            id_query_param='uid',
        ),
        SiteConfig(
            site_id='club_leggenda',
            name='CLUB LEGGENDA（クラブレジェンダ）',
            target_url='https://club-leggenda.com/list/',
            # ALYO/la_bella等と同一テンプレート(KNZ系listTypeJ)
            selector_container='div.listTypeJ li',
            selector_name='h3',
            selector_link='a[href*="profile?uid="]',
            selector_image='img.myPhoto',
            image_attr='data-original',
            id_query_param='uid',
        ),
        SiteConfig(
            site_id='grandluxe_osaka',
            name='Grand Luxe（グランリュクス）',
            target_url='https://www.grandluxe-osaka.com/therapist.php',
            selector_container='ul#list > li',
            selector_name='div.name',
            selector_link='a[href*="profile.php"]',
            selector_image='div.image img',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='yoluspa_osaka',
            name='YOLU SPA 大阪店',
            # 旧設定ではスケジュール(SCHEDULE)ページを巡回対象にしていたが、
            # このページは「その日の出勤シフト」を表示するものであり、シフトに
            # 入っていないキャストは掲載されない。そのため既存在籍者でも、たまたま
            # まだシフト表に登場していない間はknown_castsに載らず、初めて
            # シフトに入った日に毎回「新規キャスト」として誤検知されてしまう
            # (実際、シフト表の運用開始から数日で50名超の既存在籍者が次々
            # "新規"扱いされる異常な検知が続いた)。姉妹店yolu_spa
            # (mrs-yoluspa.net)と同じURLパターンの「セラピスト一覧」
            # (全キャスト常設ページ)に変更し、シフトに関わらず安定して
            # 全在籍者を検知できるようにする
            target_url='https://yoluspa-osaka.net/%e3%82%bb%e3%83%a9%e3%83%94%e3%82%b9%e3%83%88%e4%b8%80%e8%a6%a7/',
            # 既存のyolu_spa(mrs-yoluspa.net)とはドメインが異なる別店舗だが、
            # 同一テンプレート(クラス名体系)のためセレクタは流用
            selector_container='div.therapist-item',
            selector_name='a.name-link',
            selector_link='a.name-link',
            selector_image='img.therapist-image',
        ),
        SiteConfig(
            site_id='esthe_aromaone',
            name='Aroma One（アロマワン）',
            target_url='https://esthe-aromaone.com/cast/',
            # WordPress製のキャストアーカイブ(新規パターン)
            selector_container='ul.castlist > li',
            selector_name='h3.name',
            selector_link='a[href*="/cast/"]',
            selector_image='div.photo img',
        ),
        SiteConfig(
            site_id='mrs_holic',
            name='Mrs.HOLIC（ミセスホリック）',
            target_url='https://mrs-holic.com/newface.html',
            # 新規パターン: 個別の<li>ではなく<div class="onebox">がカードの単位。
            # カード全体を1つの<a>が包んでいるため直下のaをそのままリンクとして使う
            selector_container='div.newgirlbox > div.onebox',
            selector_name='div.name',
            selector_link='a',
            selector_image='div.img img',
        ),
        SiteConfig(
            site_id='iyashinadeshiko',
            name='癒しなでしこ',
            target_url='https://iyashinadeshiko.com/newface/',
            # とろりんタイム/aqua SPA等と同一テンプレート(gallist/article/h3系)
            selector_container='ul.gallist > li',
            selector_name='article h3 a',
            selector_link='article h3 a',
            selector_image='div.ph img:not(.list_today)',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='zenith_osaka',
            name='ZENITH OSAKA（ゼニス大阪）',
            target_url='https://zenithosaka.net/newface/',
            # ALYO等と同一テンプレート(KNZ系listTypeJ)
            selector_container='div.listTypeJ li',
            selector_name='h3',
            selector_link='a[href*="profile?uid="]',
            selector_image='img.myPhoto',
            image_attr='data-original',
            id_query_param='uid',
        ),
        SiteConfig(
            site_id='ore_no_ie',
            name='俺の家（おれのいえ）',
            target_url='https://ore-no-ie.com/staff.php',
            # 新規パターン: カード全体を1つの<a>が包む構造(Mrs.HOLICと同系統)。
            # サムネイル画像はNEW/体験入店バッジ用のimgと区別するためclassを明示指定
            selector_container='ul.cast_box > li',
            selector_name='p.name',
            selector_link='a',
            selector_image='img.profile_thum',
            id_query_param='sid',
        ),
        SiteConfig(
            site_id='highspec_osaka',
            name='SPAハイスペ',
            target_url='https://highspec-osaka.net/newface/',
            # KNZ系listTypeJ(ALYO等と同一テンプレート)
            selector_container='div.listTypeJ li',
            selector_name='h3',
            selector_link='a[href*="profile?uid="]',
            selector_image='img.myPhoto',
            image_attr='data-original',
            id_query_param='uid',
        ),
        SiteConfig(
            site_id='femme_fatale_osaka',
            name='Femme Fatale（ファムファタール）',
            target_url='https://femmefatale-osaka.net/newface/',
            # KNZ系listTypeJ。既存のmrs_femmefatale(Mrs.Femme Fatale/
            # mrs-femmefatale.com)とは別ドメインの別店舗のため区別して登録
            selector_container='div.listTypeJ li',
            selector_name='h3',
            selector_link='a[href*="profile?uid="]',
            selector_image='img.myPhoto',
            image_attr='data-original',
            id_query_param='uid',
        ),
        SiteConfig(
            site_id='kifujinclub',
            name='貴婦人倶楽部',
            target_url='https://kifujinclub.com/newface/',
            # KNZ系listTypeJ
            selector_container='div.listTypeJ li',
            selector_name='h3',
            selector_link='a[href*="profile?uid="]',
            selector_image='img.myPhoto',
            image_attr='data-original',
            id_query_param='uid',
        ),
        SiteConfig(
            site_id='busty_osaka',
            name='Busty Osaka（バスティオオサカ）',
            target_url='https://busty-osaka.com/therapist.php',
            # grandluxe_osakaと同一テンプレート系列(ul#list、profile.php?id=形式)
            selector_container='ul#list > li',
            selector_name='div.name',
            selector_link='a[href*="profile.php"]',
            selector_image='div.image img',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='moonr_osaka',
            name='ミセス・ムーンR',
            target_url='https://www.moonr.jp/newface/',
            # KNZ系listTypeJ
            selector_container='div.listTypeJ li',
            selector_name='h3',
            selector_link='a[href*="profile?uid="]',
            selector_image='img.myPhoto',
            image_attr='data-original',
            id_query_param='uid',
        ),
        SiteConfig(
            site_id='mrs_lavons',
            name='Mrs．LAVONS（ラボン）',
            target_url='https://mrs-lavons.com/girl',
            # kitty/Chaton/spaMAYBEと同一テンプレート系列(c-panel系)
            selector_container='div.c-panel',
            selector_name='p.c-panel__name',
            selector_link='div.c-panel__image a',
            selector_image='div.c-panel__image img',
            id_query_param='lid',
        ),
        SiteConfig(
            site_id='jukumadam',
            name='道頓堀の熟マダム',
            target_url='https://jukumadam.net/model.html',
            # 新規パターン(Shift-JISサイト。BeautifulSoupが生バイト列から
            # 自動的にエンコーディングを検出するためコード側の対応は不要)
            selector_container='div.model_list',
            selector_name='div.model_name',
            selector_link='a',
            selector_image='img.listimg',
        ),
        SiteConfig(
            site_id='pinkcompltion',
            name='pinkcompltion（ピンクコンプリ―ション）',
            target_url='https://pinkcompltion.net/model.html',
            # jukumadamと同一テンプレート(model_list系、Shift-JIS)
            selector_container='div.model_list',
            selector_name='div.model_name',
            selector_link='a',
            selector_image='img.listimg',
        ),
        SiteConfig(
            site_id='naniwajoshi',
            name='なにわ女子',
            target_url='https://naniwajoshi.com/cast/',
            # gallist系だが、petitpetit_dream等と異なりカード全体を1つの<a>が
            # 包む構造(h3に子孫の<a>は無い)。list_todayバッジ画像はdiv.phの
            # 外側の兄弟要素として配置されているため、除外指定なしでも
            # div.ph img の子孫探索で自動的に対象外となる
            selector_container='ul.gallist > li',
            selector_name='h3',
            selector_link='a',
            selector_image='div.ph img',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='mrs_luxia',
            name='Mrs.LUXIA',
            target_url='https://nipponbashi-mrsluxia.com/newface/',
            # petitpetit_dreamと同一テンプレート(umihey.sakura.ne.jp系、
            # gallist/article/h3系)
            selector_container='ul.gallist > li',
            selector_name='article h3 a',
            selector_link='article h3 a',
            selector_image='div.ph img:not(.list_today)',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='karisome_bekkan',
            name='かりそめ別館',
            target_url='https://karisome-bekkan.net/newface/',
            # KNZ(planKNZ CMS)系テンプレート。la_bella等と同一構造
            selector_container='div.listTypeJ li',
            selector_name='h3',
            selector_link='a[href*="profile?uid="]',
            selector_image='img.myPhoto',
            image_attr='data-original',
            id_query_param='uid',
        ),
        SiteConfig(
            site_id='toroten',
            name='とろてんSPA',
            target_url='https://toroten.net/newface/',
            # KNZ(planKNZ CMS)系テンプレート。la_bella等と同一構造
            selector_container='div.listTypeJ li',
            selector_name='h3',
            selector_link='a[href*="profile?uid="]',
            selector_image='img.myPhoto',
            image_attr='data-original',
            id_query_param='uid',
        ),
        SiteConfig(
            site_id='yuraku_tei',
            name='優楽亭',
            target_url='https://yuraku-tei.net/newface/',
            # petitpetit_dreamと同一テンプレート(tarao.sakura.ne.jp系、
            # gallist/article/h3系)
            selector_container='ul.gallist > li',
            selector_name='article h3 a',
            selector_link='article h3 a',
            selector_image='div.ph img:not(.list_today)',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='luxy_osaka',
            name='LUXY（ラグジー）',
            target_url='https://luxy-spa.com/therapist.php',
            # Grand Luxe系テンプレート(grandluxe_osaka等と同一構造。姉妹店として
            # ヘッダーからgrandluxe-osaka.comへ相互リンクしており同一運営と判明)
            selector_container='ul#list > li',
            selector_name='div.name',
            selector_link='a[href*="profile.php"]',
            selector_image='div.image img',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='atarispa',
            name='当たりスパ',
            target_url='https://atarispa.net/therapist/newface.php',
            # 新規パターン。name要素(article h3)内に年齢バッジ<span>が
            # 兄弟要素として同居しており、get_text()では "さな(27)" のように
            # 汚染されるため name_first_text_only=True で直下テキストのみ採用
            selector_container='li.animation',
            selector_name='article h3',
            selector_link='a[href*="/therapist/detail.php"]',
            selector_image='div.ph img',
            id_query_param='id',
            name_first_text_only=True,
        ),
        SiteConfig(
            site_id='milk_parfait',
            name='みるくぱふぇ',
            target_url='https://milk-parfait.com/girl',
            # c-panel系テンプレート(kitty等と類似)。ただしp.c-panel__nameは
            # "りこ(21)"のように年齢を含むため、代わりにp.js-eng_name span
            # (年齢を含まない<span>要素)を名前として採用
            selector_container='div.c-panel',
            selector_name='p.js-eng_name span',
            selector_link='div.c-panel__image a',
            selector_image='div.c-panel__image img',
            id_query_param='lid',
        ),
        SiteConfig(
            site_id='arrow_kyoto',
            name='ARROW京都',
            target_url='https://www.arrowkyoto.com/p/9/',
            # 新規パターン。カード全体を1つの<a>が包む構造で、詳細ページは
            # 外部サイト(estama.jp)。掲載枠によって画像が通常の<img src>と
            # lazyload用<img data-lazy-src>のどちらかに分かれているため、
            # image_attr='data-lazy-src'を指定(未指定時は自動的に'src'を
            # 試すフォールバックが働く)
            selector_container='li.grid_imgbd',
            selector_name='p',
            selector_link='a',
            selector_image='img',
            image_attr='data-lazy-src',
        ),
        SiteConfig(
            site_id='purewhite',
            name='Pure White（ピュアホワイト）',
            target_url='https://purewhite-aroma.com/cast/',
            # gallist系テンプレート(カード全体を1つの<a>が包む構造)。姉妹店
            # 「ピンクパンサー」のキャストが同一一覧に混在し、そちらは
            # pinkpanther-esthe.com側のprofile?id=を使うため、自サイトの
            # IDと衝突しうる(id_query_param使用時のドメイン別プレフィックス
            # 付与ロジックにより自動的に区別される)
            selector_container='ul.gallist li.list__item',
            selector_name='article h3',
            selector_link='a',
            selector_image='div.ph img',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='kyo_spa',
            name='京spa',
            target_url='https://esthe-kyospa.com/cast/',
            # WordPress製のキャストアーカイブ(esthe_aromaoneと同一テンプレート)。
            # div.photo内にはSNSアイコン画像も存在するため、selector_imageは
            # 詳細ページへのリンク配下のimgに絞り込む
            selector_container='ul.castlist > li',
            selector_name='h3.name',
            selector_link='a[href*="/cast/"]',
            selector_image='a[href*="/cast/"] img',
        ),
        SiteConfig(
            site_id='goodrad',
            name='グッドラッド',
            target_url='https://goodrad.men-es.jp/therapist.html',
            # men-es.jp系CMSテンプレート(cecile/restpiaと同一構造)。レイアウト
            # 調整用の空divが混在するため、リンクを持つ要素のみに絞る
            selector_container='div.staff-box:has(a)',
            selector_name='ul.box-inner li',
            selector_link='div.staff-image01 a',
            selector_image='div.staff-image01 a',
            image_from_style=True,
            id_query_param='sid',
        ),
        SiteConfig(
            site_id='spade',
            name='SPADE～スペード～',
            target_url='https://esthe-spade.com/cast/',
            # gallist系テンプレート(Pure White等と同一構造)
            selector_container='ul.gallist li.list__item',
            selector_name='article h3',
            selector_link='a',
            selector_image='div.ph img',
            id_query_param='id',
        ),
        SiteConfig(
            site_id='momoten',
            name='桃色天使2.0',
            target_url='https://momotenestama.com/therapist/',
            # 新規パターン。ページ上部の「PICK UP」プレビューと「THERAPIST LIST」
            # 本編セクションに同じキャストが重複掲載されるため、本編セクションの
            # みに絞る。name要素内には年齢が兄弟の<span>として同居するため
            # name_first_text_only=Trueを使用
            selector_container='section.p-therapist-list-section div.c-therapist-cards__item',
            selector_name='span.c-therapist-cards__name',
            selector_link='a.c-therapist-cards__link',
            selector_image='img',
            name_first_text_only=True,
        ),
        SiteConfig(
            site_id='yurikago',
            name='ゆりかご京都',
            target_url='https://yurikago-kyoto.com/therapist/',
            # 新規パターン。WordPress製。name要素内には入店日等の付加情報の後に
            # 年齢が兄弟の<span>として同居するため name_first_text_only=True
            selector_container='li.therapistbox',
            selector_name='p.therapist_name',
            selector_link='a',
            selector_image='img',
            name_first_text_only=True,
        ),
        SiteConfig(
            site_id='bimajo',
            name='The 美魔女',
            target_url='https://kyoto-esthe.com/casts/',
            # 新規パターン。name要素内の年齢が兄弟要素ではなく同一テキスト
            # ノード内にタブ文字区切りで同居する("芹沢\t\t\t(40歳)")ため、
            # name_strip_after_tab=Trueでタブ以降を除去
            selector_container='div.cast__item',
            selector_name='span.cast__name',
            selector_link='div.cast__thumb a',
            selector_image='div.cast__thumb img',
            name_strip_after_tab=True,
        ),
        SiteConfig(
            site_id='mrs_coquettish',
            name='ミセスコケティッシュ',
            target_url='https://mrs-coquettish.com/staff.php',
            # 新規パターン。p.name内は<small>Name</small>ラベルの後ろに
            # 実際の名前がテキストノードとして続く構造のため、
            # name_first_text_only=Trueで(空白のみでない)最初のテキスト
            # ノードを採用
            selector_container='ul.cast_box li',
            selector_name='p.name',
            selector_link='a',
            selector_image='img.staff',
            image_attr='data-src',
            id_query_param='sid',
            name_first_text_only=True,
        ),
        SiteConfig(
            site_id='tensai_spa',
            name='天才スパ',
            target_url='https://tensai-spa.com/girl',
            # c-panel系テンプレート(milk_parfait等と同一構造)。一部キャストは
            # 詳細ページが外部サイト(estama.jp)でlidクエリパラメータを
            # 持たないが、その場合はパス末尾から自動的にIDを生成する
            # 既存のフォールバックで対応可能
            selector_container='div.c-panel',
            selector_name='p.js-eng_name span',
            selector_link='div.c-panel__image a',
            selector_image='div.c-panel__image img',
            id_query_param='lid',
        ),
        SiteConfig(
            site_id='only_kyoto',
            name='ONLY～オンリー～',
            target_url='https://only-kyoto.net/staff.php',
            # 新規パターン。name要素内には年齢の後にSNSアイコン用の<object>が
            # 兄弟要素として同居するため name_first_text_only=True
            selector_container='ul.cast_box li',
            selector_name='dl.name p',
            selector_link='a',
            selector_image='img.staff_img',
            id_query_param='sid',
            name_first_text_only=True,
        ),
        SiteConfig(
            site_id='tiana_esthe',
            name='Ti.ana～ティアナ～',
            target_url='https://tiana-esthe.com/staff.php',
            # only_kyotoと同一運営・同一テンプレート(相互リンクあり)。
            # 画像はlazyload実装で実URLがdata-srcに入っている点が異なる
            selector_container='ul.cast_box li',
            selector_name='div.name',
            selector_link='a',
            selector_image='img.staff_img',
            image_attr='data-src',
            id_query_param='sid',
            name_first_text_only=True,
        ),
        SiteConfig(
            site_id='flowerspa_kyoto',
            name='Mrs.FlowerSpa京都・滋賀',
            target_url='https://flowerspa-kyoto.com/girllist',
            # 新規パターン。コンテナはclass指定のない裸の<div>のため親要素
            # 経由で絞り込む。詳細ページへのリンクはカード先頭の中身が空の
            # <a class="therapist_meta">がオーバーレイとして機能する
            selector_container='div.therapist_list > div',
            selector_name='h3.therapist_name',
            selector_link='a.therapist_meta',
            selector_image='p.therapist_img img',
            name_first_text_only=True,
        ),
        SiteConfig(
            site_id='mulala',
            name='MULALA（ムララ）',
            target_url='https://www.mulala-kyoto.com/therapist',
            # 新規パターン(caskan CMS)。詳細ページへのリンクがパス末尾に
            # そのままIDを含む構造のため、id_query_paramなしでも既存の
            # パス末尾フォールバックで正しくIDを取得できる
            selector_container='ul.therapist-datas > li',
            selector_name='a.therapist-datas-name',
            selector_link='div.therapist-datas-tmb a',
            selector_image='div.therapist-datas-tmb img',
        ),
        SiteConfig(
            site_id='bijuku_maria',
            name='美熟MARIA',
            target_url='https://bijuku-maria.com/newface.php',
            # 既存のo-pack.jp系CMSテンプレートに完全一致(mrs_luxe/
            # amaou_therapiと同一構造)
            selector_container='ul.ladyList li',
            selector_name='h2.ladyName',
            selector_link='a[href*="lady_detail.php"]',
            selector_image='span.thumb img',
            id_query_param='lady_num',
        ),
        SiteConfig(
            site_id='esthegame_kyoto',
            name='エステゲーム京都',
            target_url='https://esthegame.com/newface/',
            # 既存のgallist/article/h3系テンプレートに完全一致(mrs_luxia等
            # と同一構造)。list_todayバッジ画像がdiv.ph内に同居するため除外
            selector_container='ul.gallist > li',
            selector_name='article h3 a',
            selector_link='article h3 a',
            selector_image='div.ph img:not(.list_today)',
            id_query_param='id',
        ),
    ]

    # File Paths
    BASE_DIR: Path = Path(__file__).resolve().parent
    NAS_DIR_STR: str = '/mnt/nas/home_system/newface_monitor/data'  # 本環境のNASパスに適宜変更してください
    LOCAL_DIR_STR: str = str(BASE_DIR / 'data')
    MOUNT_POINT: str = '/mnt/nas'
    
    # Network Settings
    USER_AGENT: str = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
    TIMEOUT: int = 30  # seconds
    RETRY_TOTAL: int = 3
    RETRY_BACKOFF: float = 1.0

    # Notification Settings
    DISCORD_WEBHOOK_URL: Optional[str] = os.getenv('DISCORD_WEBHOOK_URL')

    # Detection Settings
    # 通常運用時の新規検知は数件〜十数件程度のため、この件数以上の差分は
    # known_castsデータの喪失/巻き戻り等による誤検知の疑いとして警告する目安値
    MASS_DETECTION_WARNING_THRESHOLD: int = 20

    # Site Failure Alert Settings
    # ネットワーク起因の巡回失敗がこの回数連続したサイトは「閉鎖・移転の疑い」
    # としてDiscordへ1回だけアラート通知し、以降の失敗ログをWARNINGに降格する
    # (1時間毎のcron実行前提で約1日分。2026-09-02のbellica閉鎖時に、消失した
    # サイトが毎時ERRORを出し続けて一次ヘルスチェックが発報し続けた事象の
    # 再発防止。詳細は _handle_site_network_failure を参照)。
    CONSECUTIVE_FAILURE_ALERT_THRESHOLD: int = 24

    @classmethod
    def get_data_dir(cls) -> Path:
        """NASアクセスを検証・修復し、動的にデータディレクトリを解決する。

        クラスロード時ではなく、実際の処理が必要になったタイミング（遅延評価）で
        マウント確認や自動修復ロジックを実行する。

        #364: 委譲先の get_managed_target_directory はNAS未マウント時に
        sudo mountによる自己修復とDiscord/LINEへの障害通知を伴う重い処理のため、
        1回の実行(_run_monitor_locked)で1回だけ呼び出し、結果をDataManagerへ
        渡して使い回すこと(サイト処理のたびに再評価しない)。

        Returns:
            Path: 利用可能なディレクトリパス
        """
        return get_managed_target_directory(
            nas_dir_str=cls.NAS_DIR_STR,
            fallback_dir_str=cls.LOCAL_DIR_STR,
            mount_point=cls.MOUNT_POINT
        )

    @classmethod
    def is_local_fallback_dir(cls, data_dir: Path) -> bool:
        """解決済みのデータディレクトリがNAS障害時のローカルフォールバック先かを判定する。

        #364: NAS未マウント時、get_data_dir()はローカルの LOCAL_DIR_STR を返す。
        ローカル側には known_casts_*.json が存在しないため、そのまま巡回を続けると
        全サイトの全在籍キャストが「新規」として再通知される(2026-09-04の
        コードレビューで指摘)。extract_youtube_urls.py の _verify_environment と
        同じく、パス正規化した上での比較で確実にフォールバック状態を検知する。

        Args:
            data_dir (Path): get_data_dir() が返したディレクトリ。

        Returns:
            bool: ローカルフォールバック先であれば True。
        """
        return Path(data_dir).resolve() == Path(cls.LOCAL_DIR_STR).resolve()


# ==========================================
# Data Models
# ==========================================

@dataclass
class CastMember:
    """キャスト情報を表現するデータクラス。

    Attributes:
        id (str): ユニーク識別子（URLパス等から生成）。
        name (str): キャスト名。
        detail_url (str): 詳細プロフィールのURL。
        image_url (str): サムネイル画像のURL。
        age (str): 年齢（数字のみ、例: "23"）。一覧ページ上に年齢表記が
            見つからないサイト・キャストでは空文字となる。
    """
    id: str
    name: str
    detail_url: str
    image_url: str
    age: str = ""

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CastMember):
            return False
        return self.id == other.id

    def to_dict(self) -> Dict[str, str]:
        """辞書形式に変換する。

        Returns:
            Dict[str, str]: JSONシリアライズ可能な辞書。
        """
        return asdict(self)


# ==========================================
# Services
# ==========================================

class DiscordNotifier:
    """Discordへの通知を担当するサービスクラス。"""

    def __init__(self, webhook_url: Optional[str]):
        """
        Args:
            webhook_url (Optional[str]): DiscordのWebhook URL。
        """
        self.webhook_url = webhook_url
        self.session = self._create_rate_limited_session()
        # 連続送信失敗時に以降の送信をスキップするサーキットブレーカー
        # (このインスタンスの生存期間=1回のプロセス実行の間だけ有効)
        self._circuit_breaker = DiscordCircuitBreaker()

    def _create_rate_limited_session(self) -> requests.Session:
        """Discordのレート制限(429)に自動追従するHTTPセッションを作成する。

        Discord WebhookはバーストしたPOSTに対して429を返すことがあり、
        毎回1秒待つだけの固定sleepでは足りずに大量のリクエストが失敗して
        通知が失われる問題があった(新人一括検知時など)。urllib3のRetryは
        429応答に付与される'Retry-After'ヘッダーを尊重して自動的に
        バックオフ・リトライするため、これに委譲する。

        Returns:
            requests.Session: 429/5xx時に自動リトライするセッション。
        """
        session = requests.Session()
        retries = Retry(
            total=MonitorConfig.RETRY_TOTAL,
            backoff_factor=MonitorConfig.RETRY_BACKOFF,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount('https://', adapter)
        return session

    def close(self) -> None:
        """保持しているHTTPセッションのリソースを明示的に解放する。"""
        if self.session:
            self.session.close()

    def notify(self, new_casts: List[CastMember], site_name: str = "") -> None:
        """新規キャスト情報をDiscordに通知する。

        Args:
            new_casts (List[CastMember]): 通知対象の新規キャストリスト。
            site_name (str): 通知元サイトの表示名。複数サイト運用時に
                どのサイトの新着かを区別できるよう埋め込みタイトルに付与する。
        """
        if not self.webhook_url or 'YOUR_DISCORD' in self.webhook_url:
            logger.warning("Discord Webhook URL is not configured. Skipping notification.")
            return

        site_prefix = f"【{site_name}】" if site_name else ""

        for cast in new_casts:
            if self._circuit_breaker.is_open:
                # 連続送信失敗によりサーキットブレーカーが開いている間は、
                # 無駄なリクエストを重ねないよう残り件数分の送信をスキップする。
                logger.warning(
                    "Discord Webhookへの連続送信失敗を検知しているため、"
                    "残りの通知をスキップします。"
                )
                break

            fields = [{"name": "Name", "value": cast.name, "inline": True}]
            if cast.age:
                # 一覧ページ上に年齢表記が見つかったキャストのみ追加
                # (見つからない場合はフィールド自体を省略する)
                fields.append({"name": "Age", "value": f"{cast.age}歳", "inline": True})
            fields.append({"name": "Link", "value": f"[詳細ページへ]({cast.detail_url})", "inline": True})

            # DiscordのembedはURL系フィールドにhttp(s)の絶対URLを要求しており、
            # data:URIや相対パス等が渡ると400 Bad Requestで通知全体が拒否される。
            # 遅延読み込み(lazyload)画像のプレースホルダー(data:image/gif;base64,...等)を
            # 誤ってimage_urlとして拾ってしまうサイトがあるため、ここで弾く。
            thumbnail_url = cast.image_url if cast.image_url.startswith(('http://', 'https://')) else ""

            payload = {
                "username": "New Face Monitor",
                "embeds": [
                    {
                        "title": f"✨ 新人キャスト情報{site_prefix}: {cast.name}",
                        "description": "新しいキャストが追加されました！",
                        "url": cast.detail_url,
                        "color": 16738740,  # Pinkish
                        "fields": fields,
                        "thumbnail": {"url": thumbnail_url} if thumbnail_url else {}
                    }
                ]
            }
            try:
                # レート制限回避のための待機（429時のバックオフはself.sessionのRetryに委譲）
                time.sleep(1)
                response = self.session.post(self.webhook_url, json=payload, timeout=10)
                response.raise_for_status()
                logger.info(f"Notification sent successfully for: {cast.name}")
                self._circuit_breaker.record_success()
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                # レスポンス本文にDiscord側の検証エラー詳細（フィールド長超過等）が
                # 含まれるため、原因究明用にログへ残す。あわせて送信元のURL系フィールドも
                # 出力し、原因切り分け（不正URL/文字数超過等）を後から追えるようにする
                body = e.response.text[:300] if e.response is not None else ""
                logger.error(
                    f"Failed to send notification for {cast.name}: {e} | body: {body} | "
                    f"detail_url: {cast.detail_url} | image_url: {cast.image_url}",
                    exc_info=True,
                )
                if status in (401, 404):
                    # Webhook自体が無効/失効している可能性が高く、残り件数分リトライしても
                    # 無駄なだけなので即座にブレーカーを開いて打ち切る。
                    logger.error(
                        f"Discord Webhook returned {status} — URL is likely invalid or revoked. "
                        "Aborting remaining notifications for this run."
                    )
                    self._circuit_breaker.trip()
                    break
                self._circuit_breaker.record_failure()
            except requests.RequestException as e:
                logger.error(f"Failed to send notification for {cast.name}: {e}", exc_info=True)
                self._circuit_breaker.record_failure()

    def notify_daily_summary(self, counts: Dict[str, int], site_names: Dict[str, str], date_str: str) -> bool:
        """その日に新規検知したサイト別件数を、テキスト形式でDiscordに通知する。

        個別キャスト通知(embed形式)とは異なり、1日分の件数をまとめた
        テキストメッセージ(content)として1件だけ送信する。

        Args:
            counts (Dict[str, int]): site_id -> 新規検知件数 の集計。
            site_names (Dict[str, str]): site_id -> 表示名 の対応表。
            date_str (str): サマリ対象日（'YYYY-MM-DD'）。

        Returns:
            bool: 送信に成功した場合True。Webhook未設定または送信失敗の場合False。
                (#226) 呼び出し元の_maybe_send_daily_summaryはこの戻り値を見て、
                成功時のみ集計をクリアする(失敗時に無条件でクリアすると、その日の
                集計がサイレントに失われ再送もできなくなるため)。
        """
        if not self.webhook_url or 'YOUR_DISCORD' in self.webhook_url:
            logger.warning("Discord Webhook URL is not configured. Skipping daily summary notification.")
            return False

        if self._circuit_breaker.is_open:
            logger.warning(
                "Discord Webhookへの連続送信失敗を検知しているため、日次サマリ通知をスキップします。"
            )
            return False

        if counts:
            total = sum(counts.values())
            lines = [
                f"・{site_names.get(site_id, site_id)}: {count}名"
                for site_id, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)
            ]
            content = (
                f"📊 **本日の新人検知サマリ ({date_str})**\n"
                f"新規検知: 合計{total}名\n\n" + "\n".join(lines)
            )
        else:
            content = f"📊 **本日の新人検知サマリ ({date_str})**\n新規検知はありませんでした。"

        # Discordのcontentは2000文字制限があるため、超過分は安全側で切り詰める
        if len(content) > 1900:
            content = content[:1900] + "\n...(以下省略)"

        payload = {"username": "New Face Monitor", "content": content}
        try:
            response = self.session.post(self.webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info(f"Daily summary notification sent successfully for {date_str}.")
            self._circuit_breaker.record_success()
            return True
        except requests.RequestException as e:
            logger.error(f"Failed to send daily summary notification: {e}", exc_info=True)
            self._circuit_breaker.record_failure()
            return False

    def notify_site_failure_alert(self, site: SiteConfig, failure_count: int) -> bool:
        """連続巡回失敗中のサイトについて「閉鎖・移転の疑い」をDiscordへテキスト通知する。

        Args:
            site (SiteConfig): 連続失敗中のサイトの設定。
            failure_count (int): 現在の連続失敗回数。

        Returns:
            bool: 送信に成功した場合True。呼び出し元はTrueの場合のみアラート
                送信済みとして記録する(失敗時は次回実行時に再試行される)。
        """
        if not self.webhook_url or 'YOUR_DISCORD' in self.webhook_url:
            logger.warning("Discord Webhook URL is not configured. Skipping site failure alert.")
            return False

        if self._circuit_breaker.is_open:
            logger.warning(
                "Discord Webhookへの連続送信失敗を検知しているため、サイト疎通不能アラートをスキップします。"
            )
            return False

        content = (
            f"⚠️ **監視サイト疎通不能アラート**\n"
            f"「{site.name}」({site.site_id}) の巡回が{failure_count}回連続で失敗しています。\n"
            f"URL: {site.target_url}\n"
            f"サイト閉鎖・ドメイン移転の可能性があります。復旧見込みが無ければ "
            f"newface_monitor.py の MonitorConfig.SITES から当該エントリを削除してください。\n"
            f"(本アラートは疎通が回復するまで1回だけ送信され、以降の失敗ログはWARNINGに降格されます)"
        )

        payload = {"username": "New Face Monitor", "content": content}
        try:
            response = self.session.post(self.webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info(f"Site failure alert sent successfully for site '{site.site_id}'.")
            self._circuit_breaker.record_success()
            return True
        except requests.RequestException as e:
            logger.error(f"Failed to send site failure alert for site '{site.site_id}': {e}", exc_info=True)
            self._circuit_breaker.record_failure()
            return False


class DataManager:
    """データの永続化と読み込みを担当するクラス。

    #364: 以前は全メソッドが静的メソッドで、呼び出しのたびに
    MonitorConfig.get_data_dir()(= core.nas_utils.get_managed_target_directory。
    NASマウント確認・sudo mountによる自己修復・Discord/LINE障害通知を伴う重い処理)
    を再評価していた。1サイトあたり最低3回、79サイトで1実行あたり240回以上に達し、
    NAS未マウント時には毎時数百回のsudo mountとDiscord投稿が発生していた。
    _run_monitor_locked が1回だけ解決した data_dir をコンストラクタで受け取り、
    以降のファイルパス導出ではNAS状態を一切再評価しない。
    """

    # 読み込み失敗とみなす例外群。UnicodeDecodeErrorはIOErrorのサブクラスではなく
    # ValueErrorのサブクラスのため、IOErrorだけを捕捉すると非UTF-8データによる
    # 破損（例: 'utf-8' codec can't decode byte ... : invalid start byte）を
    # 検知できず、同じ破損ファイルへの読み込み失敗が繰り返され続けてしまう。
    _LOAD_ERRORS = (OSError, ValueError, TypeError, KeyError)

    def __init__(self, data_dir: Path):
        """
        Args:
            data_dir (Path): 解決済みのデータディレクトリ(NAS上、または検証済みの
                ローカルパス)。呼び出し元(_run_monitor_locked)がフォールバック中で
                ないことを確認した上で渡す前提。
        """
        self.data_dir = Path(data_dir)

    def _data_file(self, site: SiteConfig) -> Path:
        """指定サイトの既知キャスト保存先JSONファイルのパスを返す。"""
        return self.data_dir / site.get_data_filename()

    @staticmethod
    def _read_casts_file(data_file: Path) -> Set[CastMember]:
        """JSONファイルを読み込み、CastMemberの集合に変換する。

        パース失敗時は例外をそのまま送出する（呼び出し側でハンドリングする前提）。
        """
        with open(data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return {CastMember(**item) for item in data}

    def load_known_casts(self, site: SiteConfig) -> Set[CastMember]:
        """指定サイトの保存済みキャストデータを読み込む。

        Args:
            site (SiteConfig): 対象サイトの設定。

        Returns:
            Set[CastMember]: 既知のキャストの集合。読み込み失敗時は空集合を返す。
        """
        data_file = self._data_file(site)
        if not data_file.exists():
            logger.debug(f"No existing data found for site '{site.site_id}'. Starting with empty state.")
            return set()

        try:
            return DataManager._read_casts_file(data_file)
        except DataManager._LOAD_ERRORS as e:
            logger.error(f"Failed to load data from {data_file}: {e}", exc_info=True)

        # 破損ファイルをそのままにすると次回以降も同じ位置で読み込みに失敗し続ける
        # ため、退避してから復旧を試みる。
        quarantine_path = data_file.with_name(
            f"{data_file.name}.corrupted-{datetime.now():%Y%m%d%H%M%S}"
        )
        try:
            data_file.rename(quarantine_path)
            logger.error(f"Quarantined corrupted cache file: {data_file} -> {quarantine_path}")
        except OSError as e:
            logger.error(f"Failed to quarantine corrupted cache file {data_file}: {e}", exc_info=True)

        # 直近の正常データがバックアップとして残っていれば、そこから復旧する
        # （空集合へのフォールバックは全キャストの再通知を招くため、可能な限り回避する）。
        backup_file = data_file.with_suffix(data_file.suffix + '.bak')
        if backup_file.exists():
            try:
                casts = DataManager._read_casts_file(backup_file)
                logger.warning(
                    f"Recovered {len(casts)} casts from backup {backup_file} after cache corruption."
                )
                return casts
            except DataManager._LOAD_ERRORS as e:
                logger.error(f"Backup file {backup_file} is also unusable: {e}", exc_info=True)

        # データ破損時は安全側に倒して空集合（再通知される可能性があるがシステム停止よりマシ）
        return set()

    def save_known_casts(self, site: SiteConfig, casts: Set[CastMember]) -> None:
        """指定サイトのキャストデータをJSONファイルに保存する。

        Args:
            site (SiteConfig): 対象サイトの設定。
            casts (Set[CastMember]): 保存対象のキャスト集合。
        """
        data_file = self._data_file(site)
        try:
            data_file.parent.mkdir(parents=True, exist_ok=True)
            data = [c.to_dict() for c in casts]

            # アトミック書き込み: 一時ファイルに書き出してから置き換えることで、
            # 書き込み中断時に既存データが破損/空になるのを防ぐ
            # (batch_download_discord.py の _purge_skipped_tasks と同じパターン)
            tmp_path = data_file.with_suffix(data_file.suffix + '.tmp')
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # 書き込んだ内容が正しく読み戻せることを検証してから本番ファイルへ反映する。
            # NAS等での書き込み中断による不可視の破損（バイト単位の欠損等）を
            # ここで検知できれば、破損データへの置き換え自体を未然に防げる。
            DataManager._read_casts_file(tmp_path)

            # 直前の正常データをバックアップとして残す。次回読み込み失敗時、
            # 空集合へのフォールバック（全キャスト再通知）を避けるために使う。
            # コピー元(data_file)は最後のreplaceまで保持したままにすることで、
            # 万一この途中でプロセスが中断しても本番ファイルは無傷のまま残る。
            if data_file.exists():
                backup_path = data_file.with_suffix(data_file.suffix + '.bak')
                try:
                    backup_path.write_bytes(data_file.read_bytes())
                except OSError as e:
                    logger.warning(f"Failed to update backup file {backup_path}: {e}")

            tmp_path.replace(data_file)

            logger.debug(f"Saved {len(casts)} casts to {data_file}")
        except (OSError, ValueError, TypeError) as e:
            logger.error(f"Failed to save data: {e}", exc_info=True)

    def _daily_summary_file(self) -> Path:
        """日次サマリの集計状態を保存するファイルのパスを返す。

        全サイト共通で1ファイルに集計するため、サイト単位のknown_casts_*.json
        とは別にトップレベルのファイルとして管理する。
        """
        return self.data_dir / 'daily_summary.json'

    def load_daily_summary(self) -> Dict:
        """日次サマリの集計状態を読み込む。

        Returns:
            Dict: {'counts': {site_id: count}, 'last_sent_date': 'YYYY-MM-DD'}
                形式の集計状態。'counts'は直近の送信以降に累積した未送信件数
                (#183参照。カレンダー日付ではなく「前回送信からの累積」で管理する)。
                ファイルが存在しない・読み込みに失敗した場合は空辞書を返す。
        """
        summary_file = self._daily_summary_file()
        if not summary_file.exists():
            return {}

        try:
            with open(summary_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except DataManager._LOAD_ERRORS as e:
            # #174: load_known_castsと同じ「非UTF-8破損でUnicodeDecodeError
            # (IOErrorのサブクラスではなくValueErrorのサブクラス)が未捕捉のまま
            # 伝播する」バグが本メソッドにも残っていた。伝播すると
            # record_daily_new_casts経由でsave_known_castsまで到達できず、
            # 毎時同じキャストが「新規」として再通知され続ける無限反復を招く。
            # _LOAD_ERRORSに統一して同じ破損パターンを確実に捕捉する。
            logger.error(f"Failed to load daily summary from {summary_file}: {e}", exc_info=True)
            return {}

    def save_daily_summary(self, data: Dict) -> None:
        """日次サマリの集計状態をJSONファイルに保存する。

        Args:
            data (Dict): 保存対象の集計状態。
        """
        summary_file = self._daily_summary_file()
        try:
            summary_file.parent.mkdir(parents=True, exist_ok=True)

            # アトミック書き込み: save_known_castsと同じパターン
            tmp_path = summary_file.with_suffix(summary_file.suffix + '.tmp')
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path.replace(summary_file)
        except IOError as e:
            logger.error(f"Failed to save daily summary: {e}", exc_info=True)

    def record_daily_new_casts(self, site_id: str, count: int) -> None:
        """サイト単位で検知した新規キャスト件数を、直近の送信以降の累積集計に加算する。

        cron等により1時間毎に別プロセスとして実行される前提のため、
        実行毎にファイルを読み書きして状態を永続化する。

        #183: 以前はカレンダー日付が変わった時点で無条件に集計をリセットして
        いたため、(1) 21時台のサマリ送信後(22時〜24時)に検知した件数が、送信
        済みにもかかわらず加算され続けた挙げ句、翌日最初の検知時のリセットで
        どのサマリにも計上されないまま消える、(2) 21時台に実行自体が無かった日
        (cron欠落・ロック競合)は日付リセットにより追い付き送信もできずその日の
        集計が丸ごと失われる、という2つの過少報告経路があった。日付によるリセットを
        廃止し、_maybe_send_daily_summaryが実際に送信した直後にのみ集計を
        クリアすることで、未送信の件数が(日付をまたいでも)必ず次回送信に
        引き継がれるようにする。

        Args:
            site_id (str): 検知元サイトのID。
            count (int): 当該サイトで新たに検知した件数。
        """
        if count <= 0:
            return

        data = self.load_daily_summary()
        counts = data.setdefault('counts', {})
        counts[site_id] = counts.get(site_id, 0) + count
        self.save_daily_summary(data)

    def _site_failures_file(self) -> Path:
        """サイト別の連続巡回失敗状態を保存するファイルのパスを返す。

        daily_summary.jsonと同様、全サイト共通で1ファイルに集約して管理する。
        """
        return self.data_dir / 'site_failures.json'

    def load_site_failures(self) -> Dict:
        """サイト別の連続巡回失敗状態を読み込む。

        Returns:
            Dict: {site_id: {'count': int, 'alerted': bool}} 形式の状態。
                'count'は現在継続中の連続失敗回数、'alerted'は閉鎖疑いアラートを
                Discordへ送信済みかどうか。ファイルが存在しない・読み込みに
                失敗した場合は空辞書を返す。
        """
        failures_file = self._site_failures_file()
        if not failures_file.exists():
            return {}

        try:
            with open(failures_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 破損等で辞書以外が保存されていた場合も安全に初期状態へ戻す
                return data if isinstance(data, dict) else {}
        except DataManager._LOAD_ERRORS as e:
            # load_daily_summaryと同様、非UTF-8破損(UnicodeDecodeError)まで
            # 含めて読み込み失敗として扱い、監視処理本体を止めない
            logger.error(f"Failed to load site failures from {failures_file}: {e}", exc_info=True)
            return {}

    def save_site_failures(self, data: Dict) -> None:
        """サイト別の連続巡回失敗状態をJSONファイルに保存する。

        Args:
            data (Dict): 保存対象の状態。
        """
        failures_file = self._site_failures_file()
        try:
            failures_file.parent.mkdir(parents=True, exist_ok=True)

            # アトミック書き込み: save_known_castsと同じパターン
            tmp_path = failures_file.with_suffix(failures_file.suffix + '.tmp')
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path.replace(failures_file)
        except IOError as e:
            logger.error(f"Failed to save site failures: {e}", exc_info=True)

    def record_site_failure(self, site_id: str) -> Tuple[int, bool]:
        """サイトの巡回失敗を1回分記録し、更新後の連続失敗状態を返す。

        Args:
            site_id (str): 失敗したサイトのID。

        Returns:
            Tuple[int, bool]: (更新後の連続失敗回数, アラート送信済みかどうか)。
        """
        data = self.load_site_failures()
        entry = data.setdefault(site_id, {'count': 0, 'alerted': False})
        entry['count'] = int(entry.get('count', 0)) + 1
        self.save_site_failures(data)
        return entry['count'], bool(entry.get('alerted', False))

    def mark_site_failure_alerted(self, site_id: str) -> None:
        """サイトの閉鎖疑いアラートを送信済みとして記録する。

        Args:
            site_id (str): アラートを送信したサイトのID。
        """
        data = self.load_site_failures()
        entry = data.setdefault(site_id, {'count': 0, 'alerted': False})
        entry['alerted'] = True
        self.save_site_failures(data)

    def clear_site_failure(self, site_id: str) -> None:
        """サイトへの疎通成功時に連続失敗状態を解消する。

        記録が無いサイトについては何もしない(毎時の正常巡回のたびに
        全サイト分のNAS書き込みが発生しないようにするため)。

        Args:
            site_id (str): 疎通に成功したサイトのID。
        """
        data = self.load_site_failures()
        if site_id not in data:
            return
        del data[site_id]
        self.save_site_failures(data)


class WebMonitor:
    """Webサイトの監視とスクレイピングを統括するクラス。"""

    def __init__(self):
        """HTTPセッションの初期化を行う。"""
        self.session = self._create_robust_session()

    def _create_robust_session(self) -> requests.Session:
        """リトライロジックを組み込んだ堅牢なHTTPセッションを作成する。

        Returns:
            requests.Session: 設定済みのセッションオブジェクト。
        """
        session = requests.Session()
        retries = Retry(
            total=MonitorConfig.RETRY_TOTAL,
            backoff_factor=MonitorConfig.RETRY_BACKOFF,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        session.headers.update({'User-Agent': MonitorConfig.USER_AGENT})
        return session

    def fetch_current_casts(self, site: SiteConfig) -> Set[CastMember]:
        """指定サイトのターゲットURLから現在のキャスト一覧を取得する。

        Args:
            site (SiteConfig): 対象サイトの設定。

        Returns:
            Set[CastMember]: 現在掲載されているキャストの集合。

        Raises:
            requests.RequestException: 通信エラー時。
        """
        try:
            # Bot検知回避のためのランダム待機
            time.sleep(random.uniform(1.0, 3.0))

            logger.debug(f"Fetching URL: {site.target_url}")
            response = self.session.get(site.target_url, timeout=MonitorConfig.TIMEOUT)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')
            return self._parse_html(soup, site)

        except requests.RequestException as e:
            # 呼び出し元でハンドリングするために再送出する。ログの重大度は
            # 連続失敗状態に応じて _handle_site_network_failure が決定するため、
            # ここでは無条件にERRORを記録しない(恒久的に消失したサイトが
            # 毎時ERRORを出し続けてヘルスチェックを発報させないようにするため)
            logger.debug(f"Network error during scraping of site '{site.site_id}': {e}")
            raise

    def _parse_html(self, soup: BeautifulSoup, site: SiteConfig) -> Set[CastMember]:
        """HTMLスープからキャスト情報を抽出する。

        Args:
            soup (BeautifulSoup): 解析対象のHTML。
            site (SiteConfig): 対象サイトの設定（セレクタ・ベースURLに使用）。

        Returns:
            Set[CastMember]: 抽出されたキャストの集合。
        """
        casts = set()
        containers = soup.select(site.selector_container)

        if not containers:
            logger.warning(
                f"No elements found matching selector: {site.selector_container} "
                f"(site: '{site.site_id}'). Layout might have changed."
            )
            return casts

        for div in containers:
            try:
                # Name Extraction
                name_elem = div.select_one(site.selector_name)
                if name_elem and site.name_first_text_only:
                    name = ""
                    for child in name_elem.contents:
                        if isinstance(child, NavigableString):
                            candidate = child.strip()
                            if candidate:
                                name = candidate
                                break
                    if not name:
                        name = name_elem.get_text(strip=True)
                elif name_elem:
                    name = name_elem.get_text(strip=True)
                else:
                    name = ""
                if site.name_strip_after_tab and '\t' in name:
                    # "芹沢\t\t\t(40歳)" のように、年齢等の付加情報が兄弟要素では
                    # なく同一テキストノード内にタブ区切りで同居しているサイト向け
                    name = name.split('\t')[0].strip()

                if not name:
                    if site.skip_unnamed_casts:
                        # 名前空のプレースホルダーカード（未公開キャスト枠等）が
                        # 一覧に混ざるサイトでは、'Unknown'として通知・登録せず
                        # カードごと読み飛ばす。後日名前付きで公開された時点で
                        # 通常の新人として検知される
                        logger.debug(
                            f"Skipping unnamed cast card on site '{site.site_id}' "
                            f"(selector: {site.selector_name})."
                        )
                        continue
                    if name_elem is not None:
                        # selector_nameはヒットしたが、テキストが空の要素だった場合
                        # (画像のみのカード等)。name_elemが見つからない場合の"Unknown"と
                        # 挙動を揃え、空文字のまま通知が送られるのを防ぐ
                        logger.warning(
                            f"Empty name extracted for a cast on site '{site.site_id}' "
                            f"(selector: {site.selector_name}). Falling back to 'Unknown'."
                        )
                    name = "Unknown"

                # Age Extraction
                # name_first_text_only/name_strip_after_tab で名前から年齢表記を
                # 切り離しているサイトでも年齢自体は失わずに取得できるよう、
                # 上記の絞り込み前のname_elem全体のテキスト(年齢の兄弟要素・
                # タブ区切り部分を含む)から抽出する
                age = ""
                if name_elem:
                    age_match = AGE_PATTERN.search(name_elem.get_text(strip=True))
                    if age_match:
                        age = age_match.group(1) or age_match.group(2)

                # Link & ID Extraction
                link_elem = div.select_one(site.selector_link)
                if not link_elem and div.name == 'a' and div.get('href'):
                    # コンテナ自体が<a>で、詳細ページへのリンクを子孫ではなく
                    # 自分自身が持っているサイト向けのフォールバック
                    # （個別の<li>等でラップされずカードそのものが<a>になっている構造）
                    link_elem = div
                detail_url = ""
                cast_id = ""

                if link_elem and link_elem.get('href'):
                    href = link_elem.get('href')
                    detail_url = urljoin(site.target_url, href)

                    if site.id_query_param:
                        # 'profile.php?id=931' のようにクエリパラメータでキャストを
                        # 識別するサイト向け: 指定パラメータの値をそのままIDとする
                        query_values = parse_qs(urlparse(href).query).get(site.id_query_param)
                        if query_values:
                            cast_id = query_values[0]
                            # 姉妹店等、自サイトとは別ドメインへのリンクが同じ一覧に
                            # 混在するサイト向け: 別ドメインの場合はIDが自サイト内の
                            # 採番と衝突しうるため、ドメイン名を付与して区別する
                            link_domain = urlparse(href).netloc
                            site_domain = urlparse(site.target_url).netloc
                            if link_domain and link_domain != site_domain:
                                cast_id = f"{link_domain}_{cast_id}"

                    if not cast_id:
                        # 'profile.html?12199' のようにキー=値形式ではなく、
                        # クエリ文字列自体（'='を含まない）がIDを表すサイト向け
                        raw_query = urlparse(href).query
                        if raw_query and '=' not in raw_query:
                            cast_id = raw_query

                    if not cast_id:
                        # パスからIDを生成 (例: /prof/123 -> 123)
                        # クエリ文字列(?utm=...等)やURLフラグメント(#...等)が付与
                        # されるとcast_idが実行ごとにブレて「新規キャスト」の
                        # 誤検知を招くため、先に除去する
                        href_no_query = href.split('?')[0]
                        href_no_fragment = href_no_query.split('#')[0]
                        clean_path = href_no_fragment.rstrip('/')
                        cast_id = os.path.basename(clean_path)

                if not cast_id:
                    # フォールバック: 名前をIDとする。ただし同一ページ内で複数件が
                    # 同時にこのフォールバックに落ちた場合（例: 名前も"Unknown"に
                    # なる要素が複数存在する）、IDが完全に同一になり
                    # Set[CastMember]内で衝突して片方が黙って失われてしまう
                    # （id/hashともにidのみに依拠しているため）。
                    # コンテナの生HTML（get_text()ではなくstr()）のフィンガープリントを
                    # 付与することで、テキストが同一/空でも画像src等の属性差異が
                    # あれば別要素として区別できるようにする。
                    fingerprint = hashlib.sha1(str(div).encode('utf-8')).hexdigest()[:10]
                    cast_id = f"name_{name}_{fingerprint}"

                if not detail_url:
                    # 個別プロフィールページへのリンクを持たないサイト向けのフォールバック:
                    # Discord通知のembed urlが空文字のまま送信されるのを避けるため、
                    # 一覧ページ自体のURLを代わりに使う
                    detail_url = site.target_url

                # Image Extraction
                img_elem = div.select_one(site.selector_image)
                image_url = ""
                if img_elem:
                    if site.image_from_style:
                        # 'background-image:url(...)' 形式のインラインCSSから抽出
                        # (<img src> ではなくCSSで背景画像として指定されるサイト向け)
                        style_match = re.search(r'url\(([^)]+)\)', img_elem.get('style', ''))
                        image_src = style_match.group(1).strip('\'"') if style_match else ""
                    else:
                        image_src = img_elem.get(site.image_attr, '')
                        if not image_src and site.image_attr != 'src':
                            # 一部の掲載枠のみ通常の<img src>を使い、他の枠は
                            # lazyload用属性を使う、といった混在サイト向けの
                            # フォールバック(指定属性が無い場合のみ'src'を試す)
                            image_src = img_elem.get('src', '')
                    if image_src:
                        image_url = urljoin(site.target_url, image_src)

                cast = CastMember(
                    id=cast_id,
                    name=name,
                    detail_url=detail_url,
                    image_url=image_url,
                    age=age
                )
                casts.add(cast)

            except Exception as e:
                # 個別のパースエラーで全体を止めない
                logger.warning(f"Error parsing specific cast element (site: '{site.site_id}'): {e}")
                continue

        logger.debug(f"Successfully parsed {len(casts)} casts for site '{site.site_id}'.")
        return casts

    def close(self):
        """リソースを明示的に解放する。"""
        if self.session:
            self.session.close()


# ==========================================
# Main Execution Flow
# ==========================================

def _handle_site_network_failure(
    notifier: DiscordNotifier, site: SiteConfig, exc: Exception, data_manager: DataManager
) -> None:
    """サイト巡回のネットワーク失敗を記録し、連続失敗が閾値に達したサイトを1回だけアラートする。

    2026-09-02にbellicaが閉鎖され(ドメインがホスティング業者のデフォルト自己署名
    証明書+ポータルサイトへの302リダイレクトに変化)、恒久的に消失したサイトが
    毎時ERRORログを出し続けて一次ヘルスチェック(health_watch)が発報し続けた。
    単発・短期のネットワーク障害は従来どおりERRORで記録しつつ、
    CONSECUTIVE_FAILURE_ALERT_THRESHOLD回連続で失敗したサイトは「閉鎖・移転の
    疑い」としてDiscordへ1回だけテキスト通知し、以降の失敗ログをWARNINGへ降格
    することで、対処済み・把握済みの消失サイトによる発報が続かないようにする。
    連続失敗の状態は疎通成功時にリセットされる(_check_site参照)ため、一時的な
    長期障害から復旧した場合は通常のERROR運用に自動的に戻る。

    Args:
        notifier (DiscordNotifier): アラート送信に使うDiscordNotifierインスタンス。
        site (SiteConfig): 巡回に失敗したサイトの設定。
        exc (Exception): 発生したネットワーク例外(ログ出力用)。
        data_manager (DataManager): 今回の実行で解決済みのデータディレクトリに
            束縛されたDataManager(#364)。
    """
    count, alerted = data_manager.record_site_failure(site.site_id)

    if not alerted and count >= MonitorConfig.CONSECUTIVE_FAILURE_ALERT_THRESHOLD:
        # 送信に失敗した場合はalertedを立てず、次回実行時に再試行する
        if notifier.notify_site_failure_alert(site, count):
            data_manager.mark_site_failure_alerted(site.site_id)
            alerted = True

    message = (
        f"Aborting monitor run for site '{site.site_id}' due to network failure "
        f"({count} consecutive failures): {exc}"
    )
    if alerted:
        logger.warning(f"{message} (closure alert already sent)")
    else:
        logger.error(message)


def _check_site(
    monitor: WebMonitor, notifier: DiscordNotifier, site: SiteConfig, data_manager: DataManager
) -> None:
    """1サイト分の巡回・差分検知・通知・保存を行う。

    サイト単位の処理を分離することで、あるサイトの通信障害・レイアウト変更が
    他サイトの監視処理に波及しないようにする。

    Args:
        monitor (WebMonitor): 使い回すWebMonitorインスタンス。
        notifier (DiscordNotifier): 使い回すDiscordNotifierインスタンス。
        site (SiteConfig): 処理対象のサイト設定。
        data_manager (DataManager): 今回の実行で解決済みのデータディレクトリに
            束縛されたDataManager(#364)。
    """
    logger.debug(f"--- Checking site '{site.site_id}' ({site.name}) ---")

    # 1. Load Data
    known_casts = data_manager.load_known_casts(site)

    # 2. Fetch Data
    try:
        current_casts = monitor.fetch_current_casts(site)
    except requests.RequestException as e:
        _handle_site_network_failure(notifier, site, e, data_manager)
        return

    # ネットワーク的に到達できた時点で連続失敗の記録があれば解消する
    # (この後のパース結果が空になるケースはセレクタ不一致等のレイアウト起因で
    # あり、閉鎖疑いを判定する疎通不能とは区別する)
    data_manager.clear_site_failure(site.site_id)

    if not current_casts:
        logger.debug(
            f"No casts found via scraping for site '{site.site_id}'. "
            "Verify selectors or site availability."
        )
        return

    # 3. Detect Diff
    new_casts_set = current_casts - known_casts
    new_casts = list(new_casts_set)

    # 既知キャストが存在するのに新規検知が大量発生した場合、known_castsの喪失/
    # 巻き戻り（NAS同期不整合やキャッシュ破損からの復旧漏れ等）による大量誤検知・
    # 再通知の可能性がある（過去にyoluspa_osakaのシフトページ誤設定で同様の事象が
    # 発生した実績あり）。通知自体は止めずに、調査の手がかりとして警告を残す。
    if known_casts and len(new_casts) >= MonitorConfig.MASS_DETECTION_WARNING_THRESHOLD:
        logger.warning(
            f"Unusually large diff for site '{site.site_id}': "
            f"{len(new_casts)} new casts vs {len(known_casts)} previously known. "
            "This may indicate known_casts data loss/rollback rather than genuine new casts."
        )

    # 4. Notify & Update
    # #237: 新規検知が無い場合にcurrent_castsで全置換すると、_parse_htmlが
    # 単発でパース失敗した既知キャスト(current_castsから漏れているだけで実際には
    # 引き続き掲載されている)がknown_castsから恒久的に消え、次回正常にパース
    # できた際に「新規キャスト」として誤って再通知される。新規検知の有無に
    # 関わらず常にunionで保存することで、既知キャストが消えないようにする。
    updated_casts = known_casts.union(current_casts)
    if new_casts:
        logger.info(f"Detected {len(new_casts)} new casts on site '{site.site_id}'.")
        notifier.notify(new_casts, site_name=site.name)
        data_manager.record_daily_new_casts(site.site_id, len(new_casts))
    else:
        logger.debug(f"No new casts detected for site '{site.site_id}'.")

    data_manager.save_known_casts(site, updated_casts)


def _maybe_send_daily_summary(notifier: DiscordNotifier, data_manager: DataManager) -> None:
    """21時台の実行のときだけ、前回送信以降に累積した新規検知サマリをDiscordへテキスト通知する。

    このスクリプトはcron等により1時間毎に別プロセスとして起動される前提
    (デーモン常駐ではない)のため、「21時になったら送る」という時刻トリガーは
    実行時刻の時(hour)が21かどうかで判定する。同日中に複数回21時台の実行が
    走った場合の重複送信を避けるため、送信済み日付をdaily_summary.jsonに
    永続化して判定に用いる。

    #183: counts は record_daily_new_casts 側でカレンダー日付によるリセットを
    行わなくなったため、ここで送信するのは「厳密な当日分」ではなく「前回この
    関数が実際に送信してから今までに累積した全件数」になる。21時台の実行が
    まる1日以上飛んだ場合(cron欠落・ロック競合)も、次に成功した21時台の実行で
    未送信分がまとめて送られる(取りこぼしなし)。送信が成功した場合のみ
    countsをクリアする。

    Args:
        notifier (DiscordNotifier): 使い回すDiscordNotifierインスタンス。
        data_manager (DataManager): 今回の実行で解決済みのデータディレクトリに
            束縛されたDataManager(#364)。
    """
    now = datetime.now()
    if now.hour != 21:
        return

    today_str = now.strftime('%Y-%m-%d')
    data = data_manager.load_daily_summary()
    if data.get('last_sent_date') == today_str:
        return

    counts = data.get('counts', {})
    site_names = {site.site_id: site.name for site in MonitorConfig.SITES}
    sent = notifier.notify_daily_summary(counts, site_names, today_str)

    # #226: 送信が失敗した(Webhook未設定/ネットワーク障害等)場合にここへ進むと、
    # 集計がクリアされ last_sent_date も当日にセットされてしまい、その日の集計が
    # 失われた上に本関数冒頭のガードで同日中の再送機会も失われる。送信成功時のみ
    # クリア・last_sent_date更新を行い、失敗時は次回実行時に再送を試みられるよう
    # 何も保存しない。
    if sent:
        data_manager.save_daily_summary({'counts': {}, 'last_sent_date': today_str})
    else:
        logger.error(
            "Daily summary notification failed; keeping accumulated counts for retry "
            "on the next run instead of clearing them."
        )


# M-7-4: 多重起動防止ロック。cron等での実行が重複すると、既知キャストリストや
# サマリファイルへの読み書きが競合し、一時消失→再通知等のデータ不整合が起きうる
# (batch_download_discord.pyでは既にflockによる同種のロックが導入済み)。
# cronの1回が想定より長く(1時間超)かかるとこの多重起動が起きやすい。
_MONITOR_LOCK_FILE_PATH = CURRENT_DIR / ".newface_monitor.lock"


def run_monitor() -> None:
    """モニタープロセスのエントリポイント。多重起動防止ロックを取得してから本処理を実行する。"""
    lock_fd = os.open(str(_MONITOR_LOCK_FILE_PATH), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        logger.info("⏭️ 他のインスタンスが既に実行中のため終了します (lock busy)")
        os.close(lock_fd)
        return

    try:
        _run_monitor_locked()
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _run_monitor_locked() -> None:
    """モニタープロセスのメインロジック。MonitorConfig.SITESに登録された全サイトを順に処理する。"""
    logger.debug("=== NewFace Monitor Started ===")

    # #364: データディレクトリはここで1回だけ解決し、DataManagerに束縛して全サイトで
    # 使い回す。get_data_dir()はNAS未マウント時にsudo mount・Discord/LINE通知を伴う
    # 重い処理のため、サイト処理のたびに再評価してはならない
    # (extract_youtube_urls.py の process_subscriptions と同じ方針)。
    data_dir = MonitorConfig.get_data_dir()

    # フェイルソフト: NASがアンマウント状態でローカルフォールバック先が返された場合、
    # ローカル側には known_casts_*.json が無く全サイトの全在籍キャストを「新規」として
    # 再通知してしまう(ストレージのウォームアップ確認はローカルディレクトリに対して
    # 必ず通過するため、ここで検知しないと防げない)。実行全体を中断する。
    if MonitorConfig.is_local_fallback_dir(data_dir):
        logger.error(
            "🚨 NASがアンマウント状態(ローカルフォールバック中)を検知しました。"
            "既知キャストデータの喪失による全キャスト再通知を防ぐため、当該バッチ処理を中断します。"
        )
        return

    # フェイルソフト: ストレージが利用できない場合は安全にタスクを終了（Exit）
    if not wait_for_storage_warmup(data_dir):
        logger.error("NASストレージへのアクセスが確立できないため、当該バッチ処理を安全に中断します。")
        return

    data_manager = DataManager(data_dir)

    monitor = None
    notifier = None
    try:
        # リソースを必要とするインスタンス化はウォームアップ確認後に実行
        monitor = WebMonitor()
        notifier = DiscordNotifier(MonitorConfig.DISCORD_WEBHOOK_URL)

        for site in MonitorConfig.SITES:
            try:
                _check_site(monitor, notifier, site, data_manager)
            except Exception as e:
                # 1サイトの予期しない例外で他サイトの処理を止めない
                logger.critical(f"Critical error while checking site '{site.site_id}': {e}", exc_info=True)

        _maybe_send_daily_summary(notifier, data_manager)

    except Exception as e:
        logger.critical(f"Critical error in NewFace Monitor: {e}", exc_info=True)

    finally:
        # 終了時のリソース解放: tryブロック内でエラーが起きても確実にCloseする
        if monitor is not None:
            monitor.close()
        if notifier is not None:
            notifier.close()
        logger.debug("=== NewFace Monitor Finished ===")


if __name__ == "__main__":
    run_monitor()