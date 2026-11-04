import type { Talent } from '../../types';

/** 顶部筛选与地图筛选共用同一份状态，保证简历、地图和表格同步刷新。 */
export interface TalentFilterState {
  education?: string;
  status?: string;
  organization_name?: string;
  province?: string;
}

export interface ProvinceTalentStat {
  name: string;
  value: number;
  highPotential: number;
}

export interface TalentViewProps {
  talents: Talent[];
  loading?: boolean;
}
