#!/usr/bin/env python3
"""Build services/fedata/fe-townsfolk.tsv: the capitals' TOWNSFOLK, where each
one lives, what they say, and a spot to stand on.

KEY: WHAT SHIPS AND WHAT DOES NOT. The client's fet_npc_type carries a second
band of capital NPCs beyond the 20 shop/manager roles: script 1xx (Beinwatt),
3xx (Azurwood), 5xx (Liberburg), 7xx (Nutsberry), 9xx (Runewall) -- named
villagers, gatekeepers (Porter_*), the free-weapon men (Kakutas / Aiai /
Roland) and Azurwood's castle guard Lionel. Their WHO ships. Their WHERE did
not (SE server data), and neither did their words.

  * DISTRICT and WORDS: atwiki.jp/10000goku/pages/243 ("Story/NPC台詞"), a
    player transcription of every capital NPC's lines per district, written
    under early Fantasy Earth Zero -- which still ran the RoD capitals (they
    were rebuilt 2008-10-27).
    The Japanese below is SE's text as transcribed; the English is OURS.
    Wiki names are katakana; each is matched to the client's identifier by
    sound and script order (e.g. カクタス = Kakutas 134).
  * WHERE: CHOSEN by this script, from the capital's own collision (the floor
    grid): clear floor away from walls, doors and the NPCs already placed,
    spread across the half. Specific people get specific places, from what
    they SAY: gatekeepers at the ends of the roads ("you may not go further"),
    the free-weapon men beside the weapon shop (Dean: "ask Kakutas, he's near
    the weapon shop"), Lionel at Azurwood's castle gate (where our invented
    guard stood, placed in game 09-06), Guinevere by the gate to Runewall's
    nobles' quarter ("through this gate lies the royal castle").

Districts (which half is which) come from the maps: Beinwatt 91 holds the
bridge market (Matthew: "shops line the bridge") = Outer; Azurwood 92 holds
the weapon shops (Aiai "stands by the weapon shop", listed under the Wall
Ward) = Wall Ward, 39 = Forest Ward (Lionel, the castle gate); Liberburg 93
lies WEST of 57 (the doors pair x=-100 in 57 with x=110 in 93) and Merle in
the Residential district says the shops are to the west, so 57 =
Residential, 93 = Commercial; Nutsberry 62's floor spans x -252..44 and 94's
-84..252, so 62 = West, 94 = East; Runewall 95 holds the shops and the gate
= Slums, 78 = Nobles' Quarter. Townsfolk the wiki does not list go to the
district whose list is empty (Beinwatt Inner, Liberburg Commercial, Runewall
Nobles') -- a guess, and the TSV says so (`source` = "none").

Not placed: `Point` (Npc45, carries OSC_SHOT_POSITION_DATA -- a camera
marker) and `EarthRed/Green/Blue/Yellow/Black` (one per nation, Managers'
flag group, no known role).

    python tools/fe_townsfolk_build.py --avoid prod_town.json         [--keep services/fedata/fe-townsfolk.tsv]

`--keep` pins everyone who already has a spot in the same half, so a rebuild
(a fixed name match, a new person) does not reshuffle people already placed
live -- the runtime op places by script and would leave them where they are
while the table pointed somewhere else.
"""
import argparse
import csv
import io
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "services"))
import fegamedata  # noqa: E402

OUT = os.path.join(HERE, "..", "services", "fedata", "fe-townsfolk.tsv")

BANDS = {1: 21, 3: 39, 5: 57, 7: 62, 9: 78}
PAIR = {21: 91, 39: 92, 57: 93, 62: 94, 78: 95}
DISTRICT = fegamedata.CAPITAL_DISTRICTS
SKIP = {"Point"}
SKIP_PREFIX = ("Earth",)

RING_JA = ("世界を最初に統一した「始まりの王」は純度の高いクリスタルから「支配の指輪」を作った。"
           "その指輪の輝きは、６つの大陸を照らすほどだったと言い伝えられているのじゃ。"
           "だが、王国内に腐敗と暴力が蔓延して、それに伴い指輪も輝きを失っていった。"
           "そして王国が滅びたときに、指輪もまた壊れてしまった。"
           "その伝説に沿って、エスセティア大陸を中心とする６つの大陸は「支配の指輪」と呼ばれているのじゃ。")
RING_EN = ("The First King, who first united the world, made the Ring of Dominion from "
           "crystal of great purity. Its light, they say, shone across all six continents. "
           "But corruption and violence spread through the kingdom, the ring lost its light "
           "with them, and when the kingdom fell the ring broke too. After that legend, the "
           "six continents around Esucetia are called the Ring of Dominion.")
WEAPON_JA = "武器がないと戦うことすらできないぞ。もし困った事があれば、俺を頼れよ。"
WEAPON_EN = "Without a weapon you can't even fight. If you're ever in a fix, come to me."
REVIVE_JA = ("戦場で倒れても、城の前で復帰できるのは知っているかな？"
             "さらには、スキルを使わない限り、少しの間は無敵でいられるんだ。覚えておくといいよ。")
REVIVE_EN = ("Did you know that if you fall on the battlefield, you get back up in front of "
             "the castle? And for a little while you can't be hurt, as long as you don't use "
             "a skill. Worth remembering.")
GOBLIN_JA = "ゴブリンブックを持っていたらわしにくれないだろうか。買い取るよ。"
GOBLIN_EN = "If you have a Goblin Book, would you let me have it? I'll buy it from you."

# name -> (half, kind, wiki katakana, line_ja, line_en). `half` None = the
# default district for its capital. kind: villager | porter | guard | weapon
# (free-weapon man) | gate | market.
PEOPLE = {
    # ---- Beinwatt, Outer (91) -- the wiki's 外部 list ----
    "Porter_Bai": (91, "porter", "ポーター・バイ",
                   "すまんが、これ以上先に進む事は許されない。もし街の外に出たいのなら、どこからでもすぐに出る事ができるぞ。",
                   "Sorry, but you may not go any further. If you want to leave town, you can head out from anywhere, right away."),
    "Oscar": (91, "villager", "オスカー",
              "な……なぜ貴族の私がこのような場所で飢えをしのがねばならんのだ。おのれ、成り上がり者のヒュンケル！今に見ているがいい……！！",
              "Wh-why must I, a nobleman, scrape by hungry in a place like this? Curse you, Hyunkel, you upstart! Just you wait...!!"),
    "Gad": (91, "villager", "ガド",
            "ウォリアーが扱える武器の種類は多彩だ。どれを極めるかはお前次第だがな。",
            "A warrior can wield all kinds of weapons. Which one you master is up to you."),
    "Kakutas": (91, "weapon", "カクタス", WEAPON_JA, WEAPON_EN),
    "Nodd": (91, "villager", "ノッド",
             "ネツァワルの民は誇り高き勇者「カカリオン」の血を引く民！飢えなどに負けはせんよ。",
             "The people of Netzawar carry the blood of the proud hero Kakarion! A little hunger won't beat us."),
    "Selma@1": (91, "villager", "シルマ",
              "昔は貴族連中がいばりくさって、あたしたちみたいな平民は、搾りとられるだけの酷い国だったんだ。でも今の王、ヒュンケル様が現れてからは、ずいぶん住みやすい国になったんだよ。",
              "The nobles used to lord it over us, and common folk like me were squeezed dry. A cruel country, it was. But since our king, Lord Hyunkel, came along, it's become a far better place to live."),
    "Matthew": (91, "market", "マシュー",
                "ここは商店街だ。橋の上に様々な商店が並んでいるんだ。壮観だろう。色々売っているから見ていくといい。あと、買い物で荷物がいっぱいになったら、バンクに行って荷物を預けられるぞ。",
                "This is the market street. All kinds of shops line the bridge -- quite a sight, eh? They sell all sorts, so take a look. And if your pack fills up, you can leave things at the bank."),
    "Ernest": (91, "villager", "エルネスト",
               "昔、この街の周囲の森には魔女が住んでいたらしいぞ。",
               "They say a witch once lived in the forest around this town."),
    "Losen": (91, "villager", "ローセン",
              "獣王の名の下に、我らに勝利を！",
              "In the name of the Beast King, victory to us!"),
    "Dean": (91, "villager", "ディーン",
             "武器がなくなった……。しかしお金が無くて補充ができない。そんな時が来るかもしれんな。だが安心しろ。国から武器を無料で支給してくれる場所があるのだ。「カクタス」という男に聞いてみろ。確か武器屋の近くにいたな。きっと力になってくれるはずだ。",
             "Your weapon's gone, and there's no money to replace it -- that day may come. But don't worry: the nation hands out weapons for free. Ask a man called Kakutas. He's near the weapon shop, I think. He'll help you."),
    "Arlene@1": (91, "villager", "アレーネ", "ちょっとそこのおぬし。婆の昔話を聞いていかんか。" + RING_JA,
                 "You there. Won't you hear an old woman's tale? " + RING_EN),
    "Evelyn": (91, "villager", "イヴリーン",
               "大昔、この国では人間も獣人も関係なく、仲良く暮らしていたそうじゃ。",
               "Long, long ago, they say, humans and beastfolk lived side by side in this country as friends."),
    "Oswald": (91, "villager", "オスワルド",
               "この国の土地はとても豊かとはいえないが、裏の鉱山ではわずかながら鉄が取れるんだ。",
               "The land here is far from rich, but the mine out back still gives up a little iron."),
    # ---- Azurwood, Forest Ward (39) -- 森の区 ----
    "Arlene@3": (39, "villager", "アーレン",
                 "我らは皆、聖女王のためならば命を惜しまぬ猛者ばかり。死など恐れはしない！",
                 "Every one of us would gladly give our life for the Holy Queen. We do not fear death!"),
    "Lionel": (39, "guard", "リオネル",
               "ここから先はカセドリア城城内だ。一般の者が立ち入る事は許されない。早々に立ち去られよ。",
               "Beyond this point lies Cesedria Castle. Ordinary folk may not enter. Kindly leave at once."),
    "Margot": (39, "villager", "マーゴット", "このじじいが支配の指輪について教えてやろう。" + RING_JA,
               "Let this old man tell you about the Ring of Dominion. " + RING_EN),
    "Bakin": (39, "villager", "ベイキン",
              "おう、そこの兵隊さん。ちょいとお願いがあるんだが……わしはこれでも読書家でな。「ゴブリンブック」を持っていたらわしにくれないだろうか。買い取るよ。",
              "Hey there, soldier, a small favour... I'm a bit of a reader, you see, and I'm after a Goblin Book. Can't make head or tail of one at a glance, but they say there's a trick to it. If you have one, let me have it? I'll buy it."),
    "Urian": (39, "villager", "ユリアン",
              "自国が支配しているフィールドのほかに自国に隣接している他国のフィールドにも入れるんだよ。戦争は国家と国家によるフィールドの支配権を巡っての総力戦だ。国同士が隣接しているフィールドでしか戦争は起こらないぞ。",
              "Besides the fields your nation holds, you can enter the enemy fields that border it, and hunt monsters there. War is different: an all-out fight between nations for a field, and it only breaks out where two nations border each other. Others can join as reinforcements, but only on the side with fewer soldiers. And monsters hide once a war begins."),
    "Mercy": (39, "villager", "マーシー",
              "確かにティファリス様はまだ幼く、とても連合国家という特殊な政務を行える方ではない。でもティファリス様はとても誠実な方なんだ。それだけでついていこうって思えるくらいね。",
              "True, Lady Tifaris is still young, hardly ready to govern something as unusual as a federation. But she is sincere through and through -- that alone makes me want to follow her."),
    "Andrea": (39, "villager", "アンドレア",
               "なんだか薄暗い世の中じゃが、ティファリス様が現れるとぱっと明るくなるような気がするんじゃ。ありがたや、ありがたや……。",
               "The world feels so gloomy, yet whenever Lady Tifaris appears, everything brightens. Bless her, bless her..."),
    "Neal": (39, "villager", "ニール",
             "ティファリス様は、由緒正しき「始まりの王」の血筋を脈々と継がれたお方なんだ。だから、人々は尊敬の念を込めてティファリス様を「聖女王」と呼ぶんだ。",
             "Lady Tifaris carries the true bloodline of the First King. That's why people call her the Holy Queen."),
    "Wallace": (39, "villager", "ワラッチ",
                "今はティファリス様しかおらんが、昔はこの土地にも多くのエルフが住んでおったんじゃよ。",
                "Now there's only Lady Tifaris, but once many elves lived in this land."),
    "Clarissa": (39, "villager", "クラリッサ",
                 "この戦いが終わったら……あの人に想いを打ち明けようと思うの。でも、この戦いはいつ終わるのかしらね。このまま想いを伝えられなかったら嫌だな……。",
                 "When this fighting is over... I'm going to tell him how I feel. But when will it ever end? I'd hate never to tell him..."),
    "Alan": (39, "villager", "アラン",
             "ほっほっ。武器はちゃんと装備するんじゃぞ。持っているだけじゃ意味がないからのう。ほっほっ。",
             "Ho ho. Be sure to equip your weapon. Just carrying it does you no good. Ho ho."),
    "Si": (39, "villager", "サイ",
           "ああ、美しきティファリス様……。身分違いとは分かっていても、この想いは募るばかりだよ……。",
           "Ah, beautiful Lady Tifaris... I know she's far above me, but my feelings only grow..."),
    "Meriel": (39, "villager", "メリエル",
               "昔、この世界を治めた「始まりの王」もまた、エルフだったという……。この国も繁栄するといいのう。",
               "They say the First King, who once ruled this world, was an elf too... May this country prosper."),
    "Jimmy": (39, "villager", "ジミー",
              "おい、そこのお前！光よりも速い、ウィンビーンの弓矢を受けてみろ！　バーン！",
              "Hey, you there! Take Winbean's arrow, faster than light! Bang!"),
    "Nahum": (39, "villager", "ナフン",
              "武器がなくなった……。しかしお金が無くて補充できない。そんな時が来るかもしれんな。だが安心しろ。国から武器を無料で支給してくれる場所があるのだ。「アイアイ」という男に聞いてみろ。確か武器屋の側にたたずんでいたはず。きっと力になってくれるだろう。",
              "Your weapon's gone, and there's no money to replace it -- that day may come. But don't worry: the nation hands out weapons for free. Ask a man called Aiai. He stands by the weapon shop, I believe. He'll help you."),
    "Guy": (39, "villager", "ガイ",
            "スキルは無尽蔵に出せるわけじゃない。状況を上手く判断して、効果的に使う事こそ必要なのだ。",
            "Skills aren't endless. Read the situation and use them where they count."),
    # ---- Azurwood, Wall Ward (92) -- 壁の区: names only on the wiki ----
    "Thelma": (92, "villager", "セルマ", "", ""),
    "Calvin": (92, "villager", "カルビン", "", ""),
    "Aiai": (92, "weapon", "アイアイ", WEAPON_JA, WEAPON_EN),
    "Ramona": (92, "villager", "ラモーナ", "", ""),
    "Lala": (92, "villager", "ララ", "", ""),
    "Pearl": (92, "villager", "ベール", "", ""),
    "Celia": (92, "villager", "セリア", "", ""),
    "Clara": (92, "villager", "クララ", "", ""),
    "Maxine": (92, "villager", "マキシ", "", ""),
    "Scott": (92, "villager", "スコット", "", ""),
    "Clyde": (92, "villager", "クライド", "", ""),
    "Victor@3": (92, "villager", "ヴィクトル", "", ""),
    # Azurwood's five gatekeepers: not on the wiki; two roads per ward, CHOSEN
    "Porter_Zera": (39, "porter", "", "", ""),
    "Porter_Libiera": (39, "porter", "", "", ""),
    "Porter_Maicost": (92, "porter", "", "", ""),
    "Porter_Jonston": (92, "porter", "", "", ""),
    "Porter_Oran": (92, "porter", "", "", ""),
    # ---- Liberburg, Residential (57) -- 居住区 ----
    "Russel": (57, "villager", "ラッセル",
               "キマイラを見たことがあるかい？獰猛な動物と魔獣を組み合わせた世界最強の生き物さ。まだ完成度が低くて、長い間生きられないのが難点らしいよ。なんだか可哀想な生き物でもあるんだよね……。",
               "Ever seen a chimera? A savage beast fused with a fiend -- the strongest creature in the world. Trouble is, they're not perfected yet and don't live long. Poor things, really..."),
    "Tallia": (57, "villager", "タリア",
               "この国には、「パンを買う金で本を買え」ってことわざがあるんだよ。まあ、食う暇も惜しんで勉強しろって事さね。あんたも頑張りなよ。",
               "There's a saying here: 'Spend your bread money on books.' Study even if it costs you meals, in other words. You do your best too."),
    "Isabel": (57, "villager", "イザベル",
               "本を一日中読んでいると目が悪くなるからね。こうやってたまに外に出ては休憩してるんだ。えっ？　いつ戻るのかって？それは内緒さ。",
               "Read all day and your eyes go bad, so now and then I come out for a break. Hm? When am I going back? That's a secret."),
    "Edward": (57, "villager", "エドワード",
               "ちゃんと魔法の勉強しなさいって母ちゃんがうるさいんだ。ボクは大きくて格好いいウォリアーになりたいんだけどなあ。",
               "Mum keeps nagging me to study my magic. But I want to be a big, cool warrior!"),
    "Owen": (57, "villager", "オーウェン", REVIVE_JA, REVIVE_EN),
    "Ludmilla": (57, "villager", "ルドミリア",
                 "死ぬまでの間に一度でいいから、戦争のない世界を見てみたいねえ。どうして若者は皆、戦争に行きたがるんじゃろうか……。",
                 "Just once before I die, I'd like to see a world without war. Why are the young ones all so eager to go off and fight...?"),
    "Ishmeal": (57, "villager", "イシュミール", "本を読んであげようか？" + RING_JA,
                "Shall I read to you? " + RING_EN),
    "Merle": (57, "villager", "マール",
              "この辺り一帯は国民が暮らす住居が立ち並んでいる。治安もいいし、住みやすい所だぞ。買い物がしたければ、西に行くと武器屋などの施設がまとまっているぞ。賑やかな所だ。行ってみるといい。",
              "This whole area is where the people live. It's safe and pleasant. If you want to shop, head west -- the weapon shops and the rest are all together there. It's lively; go and see."),
    "Wat": (57, "villager", "ワット",
            "君、リングってどんなものか知っているかい？リングというのは戦争で活躍すると貰えるものなんだけど、それを使って買い物もできるんだよ。クラスごとにリングショップがあって、リングを支払わないといけないのさ。その代わり、能力の高いアイテムが揃っているんだぜ。利用しない手はないよな！",
            "Do you know what Rings are? You earn them by doing well in the wars, and you can shop with them too. Each class has its own Ring shop that only takes Rings -- but the gear there is top grade. You'd be mad not to use it!"),
    "Abner": (57, "villager", "エイブナー", "", ""),
    "Vivian": (57, "villager", "ビビアン",
               "お腹すいたなあ……。やっぱり何か食べないと力が出ないよ……。",
               "I'm so hungry... I just can't get my strength up without something to eat..."),
    "Hugo": (57, "villager", "ヒューゴ",
             "この国では昔から漁が盛んなのじゃ。",
             "Fishing has always thrived in this country."),
    "Martin": (57, "villager", "マーティン", "", ""),
    "Anthony": (57, "villager", "アントニー",
                "戦い続けて疲れたら、座って休む。そうするとパワーを早く回復させる事ができるんじゃ。基本じゃよ。基本。",
                "When you're worn out from fighting, sit down and rest. Your Power comes back faster that way. It's the basics. The basics."),
    "Connie": (57, "villager", "コニー",
               "兵隊と戦うのはあんたたちの仕事。魚と戦うのは俺達の仕事。頑張ってくれよ！活躍したら、とびっきり美味い魚を食わしてやるからさ！",
               "Fighting soldiers is your job. Fighting fish is ours. Good luck out there! Do well and I'll feed you the finest fish you ever tasted!"),
    "Arlene@5": (57, "villager", "アーレーン",
                 "武器がないとスキルが使えないから必ず武器は装備して出かけるようにしてね。反対に言えば、スキルを装備していないと武器を使う事ができないのよ。気をつけてね。",
                 "You can't use skills without a weapon, so always head out with one equipped. And the other way round -- without skills equipped, you can't use your weapon. Take care."),
    # ---- Runewall, Slums (95) -- 貧民街 ----
    "Maurice": (95, "villager", "マーリス",
                "あらら。両手がいっぱいになっちゃったよ。バンクに預けにいかなくちゃな。",
                "Oops, my hands are full. Better go put some of this in the bank."),
    "Willam": (95, "villager", "ウィリアム",
               "ライル兄ちゃんのナイフさばきは凄いんだよ！まるで手品みたいなんだ！",
               "Big brother Lyle's knife work is amazing! It's like a magic trick!"),
    "Mill": (95, "villager", "ミル",
             "スカウトは万能だけど、敵がどんどん力押しでくるとなかなかやっかいよね。そういう時は、ソーサラーの出番ね。一発派手なのがドーンと炸裂し、敵が吹き飛ぶ……。はあ、うらやましいわ。",
             "Scouts can do anything, but when the enemy just keeps pushing with brute force, they're a real pain. That's when a sorcerer steps in -- one big blast, boom, and the enemy goes flying... Sigh. I'm jealous."),
    "Roland": (95, "weapon", "ローランド", WEAPON_JA, WEAPON_EN),
    "Melody": (95, "villager", "メロディ",
               "戦場で倒れても、城の前で復帰できるのは知っている？さらには、スキルを使わない限り、少しの間は無敵でいられるのよ。覚えておくといいわ。",
               "Did you know that if you fall on the battlefield, you get back up in front of the castle? And for a little while you can't be hurt, as long as you don't use a skill. Remember that."),
    "Cecily": (95, "villager", "セシリー",
               "なんかさっき通り過ぎていった銀髪の人……見覚えがあるんだけど。まさか……ねえ？",
               "That silver-haired person who just walked past... I feel like I know them. No... surely not?"),
    "Guinevere": (95, "gate", "ギネバル",
                  "この門を抜けると、ルーンワールの王城に行けます。買い物をしたいなら、ここから東に向かった先の商業区に行ってください。もし、戦争でリングを手に入れているのならリングショップに寄ってみてはいかがですか。リングを支払う事で強力な装備品を購入できますよ。",
                  "Through this gate lies Runewall's royal castle. For shopping, head east from here to the market. And if you've won Rings in the wars, why not visit a Ring shop? Rings buy powerful gear."),
    "Ray": (95, "villager", "ライ",
            "ライル様はあたしたちスラムのヒーローなのよ。顔もスラッとして格好いいし！キャー！",
            "Lord Lyle is the hero of us slum folk! And he's so slim and handsome! Kyaa!"),
    "Gilbert": (95, "villager", "ギルバード",
                "……おい。この辺で銀髪の怪し……いや、青年を見なかったか？くそ、どこへ行かれたんだ……。",
                "...Hey. Have you seen a suspicious silver-haired-- er, a young man around here? Damn it, where did he go..."),
    "Roil": (95, "villager", "ロイル", GOBLIN_JA, GOBLIN_EN),
    "Terence": (95, "villager", "トレンス",
                "お、いよいよ出陣か？食べ物は持ったか？腹が減ってちゃ力が出ないぜ？ちなみに、外に出たい時は、どこからでもすぐに出る事ができるぞ。",
                "Oh, heading out at last? Got food with you? You can't fight on an empty stomach. And if you want to leave town, you can head out from anywhere."),
    "Edna": (95, "villager", "エドナ",
             "ライル様が皇帝になられてから、貧富の差は少なくなったよ。だから貧民層には支持されてるけど金持ちには疎まれてるんだろうね。変わったお方だよ。",
             "Since Lord Lyle became Emperor, the gap between rich and poor has shrunk. So the poor love him and the rich resent him, I'd say. An unusual man."),
    "Liza": (95, "villager", "ライザ",
             "みんなライル皇帝の人柄ばかり取り上げるが、俺の考えは違う。ライル皇帝は、弟のケイ様の傀儡だと見てるね。兄が人気取りをして、弟が実務って感じ。皇族なんてのは、何考えてるか分かりゃしねえからな。",
             "Everyone praises Emperor Lyle's character, but I see it differently. I reckon he's his little brother Kei's puppet: the elder wins the crowds, the younger does the work. You never know what royals are thinking."),
    "Asa": (95, "villager", "エイサ",
            "ライル皇帝の母はわしの娘でな。平民だったが故に、ライルも初め、貴族とは認めてもらえんかった。それが今や一国の皇帝になった。……死んだ娘に、ひと目でいいから見せてやりたかったのう。",
            "Emperor Lyle's mother was my daughter. She was a commoner, so at first even Lyle wasn't accepted as a noble. And now he's emperor of a nation... If only my late daughter could have seen it, just once."),
    "Alvah": (95, "villager", "アルヴァン",
              "実は、たまにライル様がこのスラムにやってきて、食べ物やお金を置いていってくださるんだ。優しい王様だよ。ホントに。",
              "Truth is, Lord Lyle sometimes comes down to the slums and leaves food and money for us. A kind king. Truly."),
    "Lindsay": (95, "villager", "リンセ",
                "ライルって言えば、昔うちのガラスを壊したもんだから、尻を張り飛ばした事があるんだ。それが皇帝とはねぇ。",
                "Lyle? He broke our window once, so I tanned his backside for it. And now he's the Emperor, eh?"),
    "Luana": (95, "villager", "ラーナ",
              "ライル様は私たち貧乏人にとって、希望の星だよ。",
              "Lord Lyle is our shining star of hope, for us poor folk."),
}
# One of the 20 roster roles has its line on the wiki too: Beinwatt's Aiando
# (script 2112, slot (2, 12) -- the same slot in every capital).
ROSTER_LINES = {
    12: ("イアンド",
         "一度参加した戦争が終結しない限り、他で行われている戦争には参加できないぞ。注意してくれ！",
         "Once you've joined a war, you can't join any other until it's over. Keep that in mind!"),
}


NAME_BANDS = {}


def default_half(capital, script):
    if capital == 62:                  # Nutsberry: the wiki lists nobody
        return 62 if script % 2 else 94
    return {21: 21, 39: 92, 57: 93, 78: 78}[capital]


# ---------------------------------------------------------------- placement
def floor(area, x, z):
    return fegamedata.capital_ground(area, x, z)


def clear(area, x, z):
    """Floor here AND all round (1.5 and 3 units, 8 ways), no steps: not on a
    ledge, a wall foot or a stair edge."""
    y = floor(area, x, z)
    if y is None:
        return False
    for r in (1.5, 3.0):
        for k in range(8):
            a = k * math.pi / 4
            yy = floor(area, x + r * math.sin(a), z + r * math.cos(a))
            if yy is None or abs(yy - y) > 0.8:
                return False
    return True


def openness(area, x, z):
    n = 0
    for dx in range(-6, 7, 2):
        for dz in range(-6, 7, 2):
            if dx * dx + dz * dz <= 36 and floor(area, x + dx, z + dz) is not None:
                n += 1
    return n


def open_dir(area, x, z, fallback):
    """Face OUT into the open: the mean offset of floor within 8 units."""
    sx = sz = 0.0
    for dx in range(-8, 9, 2):
        for dz in range(-8, 9, 2):
            if (dx or dz) and dx * dx + dz * dz <= 64 and floor(area, x + dx, z + dz) is not None:
                sx += dx
                sz += dz
    if abs(sx) + abs(sz) < 6:
        sx, sz = fallback[0] - x, fallback[1] - z
    return round(math.degrees(math.atan2(sx, sz)) % 360.0, 0)


def candidates(area):
    xs, zs = [], []
    for x in range(-320, 321, 4):
        for z in range(-320, 321, 4):
            if floor(area, x, z) is not None:
                xs.append(x)
                zs.append(z)
    out = []
    for x in range(min(xs) - 2, max(xs) + 3, 2):
        for z in range(min(zs) - 2, max(zs) + 3, 2):
            if clear(area, x, z):
                out.append((float(x), float(z)))
    cx = sum(xs) / len(xs)
    cz = sum(zs) / len(zs)
    return out, (cx, cz)


def place_half(area, people, placed_rows, pinned=None):
    """people: [(script, kind)] -> {script: (x, z, yaw)}. placed_rows: the town
    file's NPC rows already in this half (kept clear of; a shop among them
    stands in for a missing minimap icon)."""
    avoid = [(float(r["x"]), float(r["z"])) for r in placed_rows]
    cands, centre = candidates(area)
    doors = [(p["gate"][0], p["gate"][1], float(p.get("radius") or 3.5))
             for p in fegamedata.area_portals(area)]
    # where a door PUTS a player in this half (SE's spawn of the partner
    # portal): nobody may stand on a landing
    landings = []
    for p in fegamedata.area_portals(area):
        q = fegamedata.portal(p["dest"])
        if q and q.get("spawn"):
            landings.append((q["spawn"][0], q["spawn"][1]))
    cands = [c for c in cands
             if all(math.hypot(c[0] - dx, c[1] - dz) >= r + 2.5 for dx, dz, r in doors)
             and all(math.hypot(c[0] - lx, c[1] - lz) >= 4 for lx, lz in landings)
             and all(math.hypot(c[0] - ax, c[1] - az) >= 4 for ax, az in avoid)]
    op = {c: openness(area, *c) for c in cands}
    taken = list(avoid)
    out = {}
    # PINNED spots (--keep): whoever already had a spot in this half keeps
    # it, so a rebuild adds people without reshuffling the ones placed live
    for sc, xzy in (pinned or {}).items():
        if any(p_[0] == sc for p_ in people):
            out[sc] = xzy
            taken.append((xzy[0], xzy[1]))
    people = [p_ for p_ in people if p_[0] not in out]

    def far(c, spacing):
        return all(math.hypot(c[0] - t[0], c[1] - t[1]) >= spacing for t in taken)

    def pick(pool, key, spacing=7.0):
        for sp in (spacing, 5.0, 3.5):
            ok = [c for c in pool if far(c, sp)]
            if ok:
                return max(ok, key=key)
        return None

    def put(name, c, face_to=None):
        taken.append(c)
        yaw = (round(math.degrees(math.atan2(face_to[0] - c[0], face_to[1] - c[1])) % 360.0, 0)
               if face_to else open_dir(area, c[0], c[1], centre))
        out[name] = (c[0], c[1], yaw)

    shops = [(i["x"], i["z"]) for i in fegamedata.shop_icons(area, trusted_only=False)
             if "Weapon_Shop" in (i.get("role") or "")]
    shops = shops or [(float(r["x"]), float(r["z"])) for r in placed_rows
                      if "Weapon_Shop" in str(r.get("name") or "")]
    specials = [p for p in people if p[1] != "villager"]
    for name, kind in specials:
        if kind == "guard":            # Lionel: Azurwood's castle gate, as placed in game
            gx, gz = LIONEL_SPOT
            taken.append((gx, gz))
            out[name] = (gx, gz, open_dir(area, gx, gz, centre))
            continue
        if kind == "porter":           # the ends of the roads, facing into town
            c = pick(cands, lambda c: math.hypot(c[0] - centre[0], c[1] - centre[1]),
                     spacing=40.0)
            if c:
                put(name, c, face_to=centre)
            continue
        if kind == "weapon" and shops:     # beside the weapon shop
            sx, sz = shops[0]
            c = pick([c for c in cands if math.hypot(c[0] - sx, c[1] - sz) <= 14],
                     lambda c: -abs(math.hypot(c[0] - sx, c[1] - sz) - 7), spacing=4.0)
            if c:
                put(name, c)
                continue
        if kind == "gate":             # Guinevere: beside the gate to the nobles' quarter
            gx, gz = doors[0][0], doors[0][1]
            c = pick([c for c in cands if math.hypot(c[0] - gx, c[1] - gz) <= 16],
                     lambda c: -math.hypot(c[0] - gx, c[1] - gz), spacing=4.0)
            if c:
                put(name, c)
                continue
        if kind == "market":           # Matthew: in the market among the shops
            mx = [(i["x"], i["z"]) for i in fegamedata.shop_icons(area, trusted_only=False)
                  if i.get("role")] or [a for a in avoid]
            if mx:
                sx = sum(m[0] for m in mx) / len(mx)
                sz = sum(m[1] for m in mx) / len(mx)
                c = pick([c for c in cands if math.hypot(c[0] - sx, c[1] - sz) <= 30],
                         lambda c: -math.hypot(c[0] - sx, c[1] - sz), spacing=5.0)
                if c:
                    put(name, c)
                    continue
        people.append((name, "villager"))     # no special spot found: a villager
    # villagers: open ground first, spread out
    mo = max(op.values()) if op else 1
    for name, kind in people:
        if kind != "villager" or name in out:
            continue

        def score(c):
            d = min((math.hypot(c[0] - t[0], c[1] - t[1]) for t in taken), default=30)
            return op[c] / mo + 0.6 * min(d, 30) / 30
        c = pick(cands, score)
        if c:
            put(name, c)
    return out


LIONEL_SPOT = (-186.8, 40.5)     # area 39: where the 09-06 guard stood at the gate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--avoid", help="a town-file JSON whose NPC rows to keep clear of")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--keep", help="a previous fe-townsfolk.tsv whose spots to keep "
                                   "(per script, when the half is unchanged)")
    a = ap.parse_args()
    town = json.load(open(a.avoid, encoding="utf-8")) if a.avoid else {}
    types = fegamedata.npc_types()
    rows = []
    halves = {}
    global NAME_BANDS
    NAME_BANDS = {}
    for v in types.values():
        s_ = v.get("script") or 0
        if 100 <= s_ < 1000 and BANDS.get(s_ // 100):
            NAME_BANDS[v["name"]] = NAME_BANDS.get(v["name"], 0) + 1
    ambiguous = [k for k in PEOPLE if "@" not in k and NAME_BANDS.get(k, 0) > 1]
    if ambiguous:
        sys.exit("PEOPLE keys shared by several capitals need @band: %r" % ambiguous)
    for tid, v in sorted(types.items(), key=lambda kv: kv[1].get("script") or 0):
        s = v.get("script") or 0
        cap = BANDS.get(s // 100) if 100 <= s < 1000 else None
        if cap is None or v["name"] in SKIP or v["name"].startswith(SKIP_PREFIX):
            continue
        # WARNING: a NAME is not unique across capitals (Selma is Beinwatt's 125 AND
        # Nutsberry's 722): a plain key only matches a name no other band
        # has; anyone else needs "Name@band". A plain "Selma" once sent both
        # Selmas to Beinwatt's Outer half.
        key = "%s@%d" % (v["name"], s // 100)
        if key not in PEOPLE and NAME_BANDS.get(v["name"], 0) == 1:
            key = v["name"]
        half, kind, kana, ja, en = PEOPLE.get(key, (None, "villager", "", "", ""))
        if half is None:
            half = default_half(cap, s)
        rows.append({"script": s, "type_id": tid, "name": v["name"],
                     "modeltype": v["modeltype"], "capital": cap, "area": half,
                     "district": DISTRICT[half][0], "district_ja": DISTRICT[half][1],
                     "kind": kind, "wiki_name": kana, "line_ja": ja, "line_en": en,
                     "source": "atwiki243" if kana else "none"})
        halves.setdefault(half, []).append((s, kind))
    missing = [k for k in PEOPLE if k.split("@")[0] not in {r["name"] for r in rows}]
    NAME_BANDS.clear()
    if missing:
        sys.exit("names in PEOPLE the client does not have: %r" % missing)
    keep = {}
    if a.keep:
        for r in csv.DictReader(io.open(a.keep, encoding="utf-8"), delimiter="	"):
            if r["script"].isdigit() and r.get("x"):
                keep[(int(r["area"]), int(r["script"]))] = (
                    float(r["x"]), float(r["z"]), float(r["yaw"]))
    spots = {}
    for half, people in sorted(halves.items()):
        placed_rows = (town.get(str(half)) or {}).get("npcs", [])
        pinned = {sc: xzy for (ar, sc), xzy in keep.items() if ar == half}
        spots[half] = place_half(half, list(people), placed_rows, pinned)
        print("area %d (%s): %d of %d placed" % (half, DISTRICT[half][0],
                                                len(spots[half]), len({n for n, _ in people})))
    cols = ["script", "type_id", "name", "modeltype", "capital", "area", "district",
            "district_ja", "kind", "x", "z", "yaw", "wiki_name", "source", "line_en",
            "line_ja"]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        sp = spots.get(r["area"], {}).get(r["script"])
        r["x"], r["z"], r["yaw"] = (sp if sp else ("", "", ""))
        w.writerow([r[c] for c in cols])
    for k, (kana, ja, en) in ROSTER_LINES.items():
        w.writerow(["roster:%d" % k, "", "", "", "", "", "", "", "roster", "", "", "",
                    kana, "atwiki243", en, ja])
    io.open(a.out, "w", encoding="utf-8", newline="\n").write(buf.getvalue())
    print("wrote %s: %d townsfolk" % (a.out, len(rows)))


if __name__ == "__main__":
    main()
