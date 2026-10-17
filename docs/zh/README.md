# Jarvis 系統規劃（中文導讀）

**定位：自主研究與遞迴自我改善的 Agent System。** 首個應用是量化／高頻交易策略研究，但目前階段**只規劃並實作 Agent 核心**，尚未接入使用者之後提供的資料、模型訓練與回測工具。

本 repository 的英文規格是 Codex 實作時的介面與驗收依據。本頁提供整體架構、工作順序與重要限制的中文解說；細節以 docs/00–14 為準。

## 一、最終要做出什麼

使用者提出研究問題，Jarvis 便能讀取已授權的策略描述與歷史研究，找出目前的問題，提出能被證偽的假設，自主設計和執行實驗，分析結果，決定要繼續、改方向或停止，然後保存完整研究紀錄。經過多次研究，它能重用已驗證的分析工具、失敗經驗與研究方法；累積足夠的 Discovery Tree 後，再讓 Dream-RSI 改善研究探索政策。

Jarvis 的三種改進不能混在一起：
1. **策略改進 L1**：模型、標籤、特徵、砍單、停利停損等策略產物變好。
2. **能力改進 L2**：Agent 學會可跨研究使用的工具、方法和知識。
3. **研究政策改進 L3**：Agent 更知道先研究哪個問題、怎麼分配預算、何時停止。

L3 是 Dream-RSI 的主要作用；它不是直接預測金融市場，也不是保證 L1 的策略報酬必定增加。

## 二、三層核心架構

**Research Control Plane**：Research Director、Scientist、Experiment Designer、Critic、Knowledge Curator，負責研究決策。第一版可由同一個 LLM 扮演不同邏輯角色，避免為了多 Agent 而增加不必要成本。

**Trusted Execution / Evaluation Plane**：Campaign Manager、Scheduler、Tool Adapter、固定 Evaluator、Artifact Store、Event Journal。真正的訓練／回測結果只由這一層產生和確認；LLM 不可自行更改正式測試集、評估門檻或偽造不存在的觀測值。

**Improvement Plane**：Evidence Memory、Capability Registry、Discovery Tree、History Pool、Replay、Dream-RSI Policy Compiler/Promotion。這一層根據研究結果累積能力，並研究如何改善研究方式。

研究 Agent 可以自主提出新問題、新方法、新程式和研究分支，但所有執行都需經過可檢查的 Tool Contract、權限和預算。

## 三、單次自主研究的循環

~~~text
Research Goal
   ↓
檢查任務限制、資料能力與 Baseline
   ↓
分析失敗、形成多個研究假設
   ↓
Research Frontier：列出目前可執行的研究行動
   ↓
Q0（未來可被 Dream-RSI 改善）選擇分支與預算
   ↓
Experiment Agent 設計／實作候選
   ↓
受控工具執行訓練、回測或分析
   ↓
固定 Evaluator 計算結果，Critic 分析與反駁
   ↓
根據證據：繼續／開新分支／停止／要求資料
   ↓
保存實驗、失敗、成本、程式版本與證據
   ↓
正式評估、報告、建立可重用 Knowledge/Capability
~~~

這不是固定把 100 組超參數全部跑完的搜尋。若連續調整模型無效，Agent 可以根據分析決定改研究 Target、實驗設計或決策門檻；但研究主目標和正式評估器不可因為成績不好而被偷偷更換。

## 四、Dream-RSI 的真正機制

在第一次研究時，就記錄完整 **Discovery Tree**：每次決策有哪些選項、當時看到什麼證據、實際執行什麼、結果、成本、兄弟分支、程式版本。之後不同的 Research Policy Q 可以在這些**已實際執行過的節點**上進行 Replay，選擇不同順序、分支或停止時間，而不用重新執行全部模型訓練。

重要限制：沒有被執行過的分支，結果是 UNKNOWN，不能用推測的 PnL 冒充實測。歷史 Replay 只是在過去研究空間裡的比較；若要宣稱 Jarvis 真的變得更會研究，需要在新的研究任務、相同研究預算下驗證。

Dream-RSI 流程：線上研究 → Discovery Tree → History Pool → 離線比較多個 Q → 僅選一個通過獨立保留任務的候選 → 在新的研究 Campaign 使用 → 持續收集更豐富的研究樹。

## 五、記憶不是單純 Vector Database

每個結論必須保存實驗、可用資料範圍、假設、支持與反對證據，以及什麼情況下不適用。工具需要保留版本、輸入輸出、測試與能力生命週期。能力並非寫了程式就能視為有效；需要獨立驗證，而且新的 Session 應可自己發現並正確使用。

下一個策略或新的研究主題可以檢索以前的經驗，但必須先檢查適用性。負面實驗也應保留，以避免系統浪費資源重做同樣失敗的研究。

## 六、如何交由本機 Codex 實作

- **P0**：資料契約、持久化、工具驗收、合成研究環境、固定評估器、Q0、Discovery Tree。
- **P1**：Research Agent 多步自主研究、根據結果改方向、中斷恢復、完整報告。
- **P2**：結構化 Memory、Capability Creation、跨 Session 使用和 A/B 比較。
- **P3**：建立多任務研究語料，驗證跨研究的知識重用，收集多分支 Discovery Tree。
- **P4**：Dream-RSI DSL、History Pool、Replay、Policy Development、獨立測試和新任務驗證。
- **P5**：使用者提供真實訓練與回測系統資訊後，以 Adapter 接入金融領域，不重寫底層工具。

第一階段用**可重現的合成任務**證明 Agent 系統運作，不能憑空產生 BTC 策略收益。等使用者提供真正的工具介面後，再依 docs/13_ADAPTER_BOUNDARY.md 完成對接。

## 七、交付標準

每階段要求可執行程式、pytest、真實命令輸出、不可變研究證據、明確狀態和限制。不應只交出大量空目錄、假 API 或一份宣稱「完成」卻沒有跑測試的報告。

Jarvis GitHub 為**公開 repository**，請勿推送私有資料、實際策略程式、金鑰、模型權重或從其他 private repo 大量複製的原始碼。

詳細入口：[根目錄 README](../../README.md) · [Codex 指令 AGENTS.md](../../AGENTS.md) · [實作階段](../09_ROADMAP.md) · [Dream-RSI 規格](../05_DREAM_RSI.md) · [測試矩陣](../10_TEST_PLAN.md)。
