export type TargetModeId = 'generic' | 'openai' | 'anthropic' | 'google'

export interface TargetModeOption {
  readonly id: TargetModeId
  readonly label: string
  readonly summary: string
  readonly plannedMilestone: string
}

export interface PlannedControlGroup {
  readonly id: string
  readonly label: string
  readonly options: readonly string[]
  readonly defaultOption: string
}

export interface EvaluationRoundStage {
  readonly round: number
  readonly title: string
  readonly description: string
}

export interface ArchitecturePillar {
  readonly id: string
  readonly title: string
  readonly description: string
  readonly milestoneTag: string
}

export const TARGET_MODE_OPTIONS: readonly TargetModeOption[] = [
  {
    id: 'generic',
    label: 'Universal',
    summary: 'Model-neutral structured Markdown output via GenericPromptRenderer.',
    plannedMilestone: 'M6',
  },
  {
    id: 'openai',
    label: 'ChatGPT / GPT',
    summary: 'OpenAIPromptRenderer formatting aligned with hierarchical Markdown conventions.',
    plannedMilestone: 'M8.5',
  },
  {
    id: 'anthropic',
    label: 'Claude',
    summary: 'AnthropicPromptRenderer structured sections with explicit delimiter boundaries.',
    plannedMilestone: 'M8.5',
  },
  {
    id: 'google',
    label: 'Gemini',
    summary: 'GeminiPromptRenderer layout preserving Engineer-authored examples and constraints.',
    plannedMilestone: 'M8.5',
  },
]

export const PLANNED_CONTROL_GROUPS: readonly PlannedControlGroup[] = [
  {
    id: 'prompt-detail',
    label: 'Prompt Detail Level',
    options: ['low', 'medium', 'high'],
    defaultOption: 'medium',
  },
  {
    id: 'response-detail',
    label: 'Response Detail Preference',
    options: ['minimal', 'balanced', 'comprehensive'],
    defaultOption: 'balanced',
  },
  {
    id: 'reasoning-preference',
    label: 'Reasoning Preference',
    options: ['auto', 'low', 'medium', 'high'],
    defaultOption: 'auto',
  },
]

export const EVALUATION_ROUND_STAGES: readonly EvaluationRoundStage[] = [
  {
    round: 1,
    title: 'Evaluation 1 — Initial Generation & Critique',
    description:
      'Prompt Engineer produces CanonicalPrompt v1; Prompt Critic evaluates independently.',
  },
  {
    round: 2,
    title: 'Evaluation 2 — First Targeted Revision',
    description:
      'Triggered only if Round 1 misses quality gates; revises the highest-scoring version.',
  },
  {
    round: 3,
    title: 'Evaluation 3 — Second Revision & Hard Stop',
    description:
      'Final permitted evaluation round; workflow deterministically terminates afterward.',
  },
]

export const ARCHITECTURE_PILLARS: readonly ArchitecturePillar[] = [
  {
    id: 'independent-critic',
    title: 'Independent Quality Assessment',
    description:
      'Prompt Engineer and Prompt Critic execute with separate instructions and configurable models. No agent can approve its own output.',
    milestoneTag: 'Planned · M6–M7',
  },
  {
    id: 'deterministic-bounds',
    title: 'Deterministic 3-Round Revision Cap',
    description:
      'Weighted rubric totals are calculated in code rather than by an LLM, and every optimization run stops after at most three evaluations.',
    milestoneTag: 'Planned · M7–M8',
  },
  {
    id: 'canonical-compiler',
    title: 'CanonicalPrompt & Target Renderers',
    description:
      'Structured prompt content is compiled deterministically for Universal, ChatGPT / GPT, Claude, or Gemini without inventing unverified content.',
    milestoneTag: 'Planned · M6 & M8.5',
  },
]

