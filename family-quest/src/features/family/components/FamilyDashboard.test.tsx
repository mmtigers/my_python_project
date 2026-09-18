/**
 * Issue #659 改善案1(d): 横画面4人パネルのクールダウン独立性。
 *
 * 既存の `QuestList.test.tsx`(#363) は QuestList を**1枚ずつ**描画して検証して
 * いるが、実際に問題が起きるのは「同じ completedSignal を4枚同時に受け取った
 * とき、兄のパネルだけがクールダウンに入る」という組み上がりの側である。
 * ここでは本物の FamilyDashboard を4人ぶん描画して、その独立性を固定する。
 *
 * completedSignal / processingQuestKeys が App から FamilyDashboard →
 * FamilyPanel → QuestList へどう届くか(props か Context か)を変えても、
 * この観察結果は変わってはいけない。
 */
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CompletedSignal, Quest, User } from '@/types';

import { SettingsProvider } from '@/context/SettingsContext';
import { ToastProvider } from '@/context/ToastContext';
import { QuestActivityProvider } from '../../quest/context/QuestActivityContext';
import { getQuestProcessingKey } from '../../quest/hooks/useQuestStatus';

vi.mock('@/hooks/useSound', () => ({ useSound: () => ({ play: vi.fn() }) }));
vi.mock('@/hooks/useRoutineData', () => ({
  useRoutineData: () => ({ flows: [], completeStep: vi.fn(), isCompleting: false }),
}));

// モック定義より後でないと FamilyDashboard が掴む参照が差し替わらない
import FamilyDashboard from './FamilyDashboard';

const user = (id: string, name: string, role: User['role']): User =>
  ({ user_id: id, name, role, level: 1, exp: 0, gold: 0 }) as User;

const DAD = user('dad', 'パパ', 'role_adult');
const MOM = user('mom', 'ママ', 'role_adult');
const SON = user('son', 'ともや', 'role_child');
const DAUGHTER = user('daughter', 'ゆい', 'role_child');

// target_user: 'all' なので4人全員のパネルに同じカードが出る
const SHARED_QUEST: Quest = {
  quest_id: 10,
  title: '食器の片付け',
  quest_type: 'infinite',
  target_user: 'all',
  gold_gain: 5,
  icon_key: '🍽️',
};

function renderDashboard(overrides: {
  completedSignal?: CompletedSignal | null;
  processingQuestKeys?: string[];
} = {}) {
  render(
    <SettingsProvider>
      <ToastProvider>
        <QuestActivityProvider
          completedSignal={overrides.completedSignal ?? null}
          processingQuestKeys={overrides.processingQuestKeys ?? []}
          busyHistoryIds={[]}
        >
          <FamilyDashboard
            users={[DAD, MOM, SON, DAUGHTER]}
            quests={[SHARED_QUEST]}
            completedQuests={[]}
            pendingQuests={[]}
            rewards={[]}
            onQuestClick={vi.fn()}
            onBuyReward={vi.fn()}
            onApprove={vi.fn()}
            onReject={vi.fn()}
            onApproveAll={vi.fn()}
            onAvatarClick={vi.fn()}
          />
        </QuestActivityProvider>
      </ToastProvider>
    </SettingsProvider>
  );
}

/** 指定メンバーのパネル(名前を含む最も近い祖先)を取り出す。 */
function panelOf(name: string): HTMLElement {
  const label = screen.getByText(name);
  const panel = label.closest('div.flex.flex-col');
  if (!panel) throw new Error(`${name} のパネルが見つからない`);
  return panel as HTMLElement;
}

afterEach(() => {
  cleanup();
});

describe('FamilyDashboard: 4パネルの同時描画', () => {
  it('4人ぶんのパネルに同じ共有クエストが1枚ずつ出る', () => {
    renderDashboard();

    // 無限クエストのタイトルは「(N回目)」付きで出る(useQuestStatus の displayTitle)
    expect(screen.getAllByText('食器の片付け (1回目)')).toHaveLength(4);
    for (const name of ['パパ', 'ママ', 'ともや', 'ゆい']) {
      expect(within(panelOf(name)).getByText('食器の片付け (1回目)')).toBeInTheDocument();
    }
  });
});

describe('FamilyDashboard: クールダウンの独立性 (#363)', () => {
  it('完了した本人のパネルだけがクールダウンに入る', () => {
    renderDashboard({ completedSignal: { id: 10, userId: 'son', nonce: Date.now() } });

    // 兄だけが "Wait..."。同じ completedSignal を受け取る他の3枚は操作可能なまま
    expect(within(panelOf('ともや')).getByText('Wait...')).toBeInTheDocument();
    expect(screen.getAllByText('Wait...')).toHaveLength(1);
  });

  it('別のクエストidの完了では誰もクールダウンに入らない', () => {
    renderDashboard({ completedSignal: { id: 99, userId: 'son', nonce: Date.now() } });

    expect(screen.queryByText('Wait...')).not.toBeInTheDocument();
  });

  it('completedSignal が無ければ4枚とも操作可能', () => {
    renderDashboard();

    expect(screen.queryByText('Wait...')).not.toBeInTheDocument();
  });
});

describe('FamilyDashboard: 送信中表示の独立性 (#391)', () => {
  it('送信中のキーを持つ本人のパネルだけが送信中になる', () => {
    renderDashboard({ processingQuestKeys: [getQuestProcessingKey('son', 10)] });

    expect(within(panelOf('ともや')).getByText('送信中...')).toBeInTheDocument();
    expect(screen.getAllByText('送信中...')).toHaveLength(1);
  });

  it('複数人が同時に送信中でも、その人数ぶんだけが送信中になる', () => {
    renderDashboard({
      processingQuestKeys: [getQuestProcessingKey('son', 10), getQuestProcessingKey('mom', 10)],
    });

    expect(screen.getAllByText('送信中...')).toHaveLength(2);
    expect(within(panelOf('ゆい')).queryByText('送信中...')).not.toBeInTheDocument();
  });

  it('進行中のキーが無ければ誰も送信中にならない', () => {
    renderDashboard();

    expect(screen.queryByText('送信中...')).not.toBeInTheDocument();
  });
});
