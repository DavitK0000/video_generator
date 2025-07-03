import re
def split_text_into_chunks(
    text: str,
    chunks_count,
    word_limit=10,
) -> list:
    """
    Split text into chunks based on sentences, respecting word limit per chunk.

    Args:
        text: Input text to be split
        word_limit: Maximum number of words per chunk
        chunks_count: Maximum number of chunks to return

    Returns:
        List of text chunks
    """

    # Clean the text (similar to JavaScript version)
    raw = text
    cleaned = raw.replace("\\n", "\n")  # Convert literal \n into real newlines
    cleaned = re.sub(r"\s+", " ", cleaned)  # Collapse multiple spaces/newlines
    cleaned = cleaned.strip()

    # Check if text contains CJK characters
    has_cjk = bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff]', cleaned))
    
    if has_cjk:
        # For CJK languages, use character-based chunking
        # Split into sentences using CJK punctuation
        sentences = re.findall(r'[^。！？…；，]+[。！？…；，]*', cleaned)
        
        # Remove empty sentences and clean up
        sentences = [s.strip() for s in sentences if s.strip()]
        
        if not sentences:
            sentences = [cleaned]
        
        chunks = []
        current_chunk = ""
        char_limit = word_limit * 3  # For CJK, use more characters per chunk
        
        for sentence in sentences:
            sentence = sentence.strip()
            
            # If adding this sentence would exceed the limit, start a new chunk
            if len(current_chunk) + len(sentence) > char_limit and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = sentence
            else:
                if current_chunk:
                    current_chunk += sentence
                else:
                    current_chunk = sentence
        
        # Add the last chunk if there's content left
        if current_chunk:
            chunks.append(current_chunk.strip())
            
    else:
        # For non-CJK text, use word-based splitting
        # Split into sentences - support both Western punctuation
        sentences = re.findall(r'[^\.!\?]+[\.!\?]+(?:\s|$)', cleaned) or []
        
        # Fallback: if no sentences found, split by newlines or use entire text
        if not sentences:
            sentences = [line.strip() for line in cleaned.split('\n') if line.strip()]
            if not sentences:
                sentences = [cleaned]

        chunks = []
        current_words = []

        for sentence in sentences:
            sentence = sentence.strip()
            sentence_words = sentence.split()
            
            if len(current_words) + len(sentence_words) <= word_limit:
                current_words.extend(sentence_words)
            else:
                if len(current_words) > 0:
                    chunks.append(" ".join(current_words))
                current_words = sentence_words

        # Add the last chunk if there are any words left
        if current_words:
            chunks.append(" ".join(current_words))

    # Limit the number of chunks returned
    if chunks_count == -1:
        return chunks

    return chunks[:chunks_count]

text = """**盗贼夜间潜入农场，农夫却出其不意**

你有没有想过，一个看似平静的夜晚，黑暗会隐藏着怎样的阴谋？那是一个星空璀璨的夜晚，微风轻拂，然而在这静谧的外表下，却潜藏着紧张的气氛。就如同一场藏匿的风暴，随时可能席卷而来。在这片农田上，一组目无王法的盗贼，悄然无息地向目标逼近，然而，他们却未曾料到，等待他们的将是一位智慧与勇气并存的农夫。

故事的主角，张农夫，一个传统农民，性格沉稳而坚韧，勤耕苦作。他的农场是他生命中的一部分，每一寸土地、每一颗庄稼，都是他辛劳的结晶。然而，近几天，他的农场却接连遭到盗窃，损失惨重。张农夫内心充满了愤怒，也充斥着不安。他发誓，要找出这个无耻的窃贼。

就在这样一个夜晚，张农夫决定采取行动。他不再是那个默默耕耘的人，而是转身成为了捍卫自己财产的斗士。他熟悉农场的每一个角落，知道如何利用黑暗与影子与盗贼周旋。他的心如同平静的湖面，内心却早已翻起了波澜。张农夫设下了重重埋伏，精心布局，恰如一位智勇双全的将领，等待着敌人的到来。

时间缓缓流逝，夜色愈发浓厚。此时，一阵窸窣的声音划破了寂静，马厩的门悄然开启，三个身形瘦削的盗贼小心翼翼地潜入。黑暗中的他们，充满了自信，仿佛捡到了天上掉下来的馅饼。然而，他们却无法感知，隐藏在农场每一个角落的目光，正如猎手注视着猎物。

当盗贼开始翻找柜子，沟渠的水声似乎也在讥讽他们的无知。张农夫默默注视着他，心中闪过一丝冷笑。他们不知道，这片土地有多了解它的主人。张农夫本能地感受到，今晚这场游戏的胜负，不仅关乎他失去的财物，更关乎人性深处的对峙与博弈。

"噔噔"的脚步声在夜幕中渐渐靠近，张农夫静静地等着，而盗贼的窃笑声愈加明显。他们小觑了这位老农夫，以为他只是个易于捏扁的乡下人。不，他们错了。张农夫不仅是农田的守护者，更是人与人之间智慧较量的参与者。

就在盗贼们意犹未尽时，张农夫终于发出了他的第一声声音，不是怒吼，而是一声低沉而富有力量的警告："你们在干什么？"这句话如同雷霆般直击盗贼的心灵，瞬间击穿了他们的狂妄自大，面面相觑，恐惧的阴影迅速席卷他们的脸庞。

张农夫的眼神闪烁着坚定的光芒。他在黑暗中如同一面坚不可摧的盾牌，浑然不惧。此时此刻，盗贼们意识到，他们不仅仅是在对抗一位农夫，而是在与一个更复杂的人性挑战较量。紧接着，农夫的动作如同猎豹般迅猛，准备迎接这场没有硝烟的战争。

**盗贼夜间潜入农场，农夫却出其不意**

你能想象在一个寂静无声的夜晚，黑暗中的偷袭者与深藏智慧的庄稼人，展开一场心理与勇气的较量吗？在这片幽深的田野上，一个无声的搏斗正在酝酿。今晚，是命运的转折点。盗贼们自信地潜入，认为农夫只是一个低头埋头劳作的乡下人，却没有意识到，他的农场是他灵魂的庇护所。

他们来得很轻松，心中满是侥幸和得意。可他们不知道，张农夫就是个披着羊皮的狡猾猎手。在过去的几天里，他的农场频频遭到袭扰，庄稼被掠走，牲畜也时常神秘失踪。张农夫内心焦虑，却又无可奈何。可他从未想过就这样轻言放弃。这块土地与他的命运紧密相连。他决定，今晚要改变这一切。

夜幕降临，张农夫早已埋伏在黑暗之中。他不是那种蛮干的人，而是个深思熟虑、计划周全的智者。他在每一片田地中，把农具与工具布置成出其不意的陷阱，正如棋盘上的棋子，等待着敌人的到来。月光透过云层，投下淡淡的光影，恰似斗争的前奏。

与此同时，几个盗贼悄然接近，看似默契十足，但内心的紧张掩盖不了他们的无知。随着他们的脚步声越来越近，张农夫的心跳也与之共振。他知道，这不仅关乎金钱，更关乎信念与尊严。

"就让他们享受这一刻的放松吧。"他暗自思忖，嘴角微微上扬，透出一丝狡黠的笑意。正当盗贼们得意忘形，翻找着仓库里的饰品与工具时，张农夫的声音传来，低沉而坚定，仿佛自地狱而来："你们在干什么？"这句话在寂静的夜中爆炸般回响，瞬间唤醒了盗贼们的恐惧。

张农夫并不打算直接与他们交锋，而是运用心理战术，将他们置于不利地位。盗贼们慌忙转身，瞪大了眼睛，内心充满了恐慌和不安。他们以为自己是猎人，但此刻却成了张农夫手中的猎物。农夫的每一个动作，都是策略与力量的展现，他在将敌人逼入绝境。

盗贼们开始在暗中寻找出路，却只感受到眼前一片迷茫。他们甚至无法想象，张农夫的智慧与勇气，将如何化解他们的阴谋。张农夫用他所掌握的每一个细节，开始逐步逼近，像是在玩一场博弈，既要保持冷静，又要抓住时机。

此时，张农夫想起了自己无数个失眠的夜晚，想起了那些为生活奔波而流下的汗水。他心中燃起一股力量，仿佛在向这个世界宣战："我是这个土地的守护者，绝不能让你们轻易得逞！"

而盗贼们正在逐渐感受到无形中的压力。他们原本骄傲的面孔开始变得苍白。就在此时，张农夫决定发动最后的攻势。他暗自凝神，准备将他们一一抓住，迎接这场不可避免的对抗。



**盗贼夜间潜入农场，农夫却出其不意**

在漆黑的夜幕下，潜藏着多少阴暗的角落和无法言说的秘密？你能想象，在灯火阑珊的乡间，悄然降临的夜晚，竟会是一场智勇的比拼？在这片寂静的农场上，两个截然不同的人生轨迹，即将交汇。一个是自以为是的窃贼，另一个则是坚定不移的农夫，他们之间，将展开一场前所未有的较量。

故事的主人公，李农夫，心中满怀愤怒和焦虑。最近，他的农场接连遭到盗贼的袭扰，损失惨重。每一颗庄稼、每一头牲畜，都是他辛勤劳作的结晶，却在黑暗中被无情掠夺。这个看似平常的夜晚，却注定要改写他们的命运。李农夫明白，自己不能再坐以待毙。于是，他决定出手反击，展开一场逆袭。

夜深人静，农场静谧如泉，唯有月光洒下，映照着一片坚韧的土地。而此刻，几个匆匆忙忙的盗贼正悄无声息地潜入，贪婪的欲望充斥着他们的内心。他们欢快地窃笑，觉得自己将轻松得手，然而他们却未曾料到，面前这片土地所隐含的惊人秘密。

李农夫早已在黑暗中暗中观察。他虽然身材佝偻，但在这片土地上，他是一位战士。他知道每一个草丛的藏身之处，懂得如何利用环境与黑暗来与盗贼周旋。李农夫并不是简单的农人，他的智慧与坚韧早已超越了那些无知的窃贼。他暗自盘算，今晚的战斗不仅关乎金钱，更是对人性尊严的捍卫。

盗贼们试图发出商议声音，讨论着如何分赃，却在一瞬间被无形的压力所笼罩。当李农夫缓缓走出阴影，冷冷一笑，低声说道："你们以为可以在这里肆无忌惮吗？"这句简单的话语宛如驴打滚般将盗贼们震慑得面面相觑，心中则生出一丝恐惧。

李农夫并不急于交手，相反，他知道，心理的博弈才是最重要的。他冷静地分析着对方的反应，等待着最佳时机。他深知这场游戏的关键在于如何引导情绪，如何让对手陷入自己的设局中。就在盗贼们不知所措之际，他适时地施加了额外的压力，进一步让他们动摇。

四周的空气逐渐变得紧张，盗贼们的心跳声开始加快，仿佛在不断敲击着他们的恐惧。这时，李农夫语速骤然加快，声音如同雷鸣："你们在这里等待的，不是财富，而是绝望！"

这一刻，整个农场仿佛化为一个战场。张农夫的冷静与坚定与盗贼的贪婪无知形成了鲜明的对比，让对方在心理上完全失去了主导权。每一步都显得如此精准、毫不犹豫。在这场紧张的博弈中，李农夫用他的智慧与勇气，逐渐扭转了局势。随着时间的推移，斗地主之局也在悄然变化。"""

print(split_text_into_chunks(text, -1, 400))