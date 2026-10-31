import type { ComponentBinding, PersistedConstraints, TripDocument } from './features/agentic-editor/types';
export type DayKey = `day${number}`;
export type RegionId = `day-${number}`;
export type ViewType = 'cards' | 'timeline';
export type TargetRegion = RegionId | 'all';
export type TransportMode = 'transit' | 'driving' | 'walking';
export interface ContentBlock {
  id: string;
  title: string;
  type: 'note' | 'checklist' | 'table';
  text: string;
  items: string[];
  columns: string[];
  rows: string[][];
}
export interface AgentStatus {
  mode: 'llm' | 'demo';
  configured: boolean;
  model: string;
  modelLabel: string;
  provider: string;
  capabilities: string[];
  mapConfigured: boolean;
}
export interface RoutePoint {
  stopId: string;
  name: string;
  status: 'resolved' | 'unresolved' | 'fictional';
  lng?: number;
  lat?: number;
  searchUrl: string;
}
export interface RouteSegment {
  fromStopId: string;
  toStopId: string;
  fromName: string;
  toName: string;
  mode: TransportMode;
  status: 'ready' | 'unavailable';
  durationMinutes: number | null;
  distanceMeters: number | null;
  summary: string;
  path: [number, number][];
  navigationUrl: string | null;
}
export interface RouteDay {
  status: 'ready' | 'partial' | 'unconfigured' | 'unavailable';
  message: string;
  provider: 'amap';
  queriedAt: string;
  points: RoutePoint[];
  segments: RouteSegment[];
}
export interface AgentTrace {
  step: string;
  status: string;
  detail: string;
}

export interface Place {
  id: string;
  name: string;
  category: string;
  indoor: boolean;
  durationMinutes: number;
  cost: number;
  transportCost: number;
  description: string;
  location: string;
  address?: string;
  mapQuery?: string;
  sourceUrl?: string;
  sourceName?: string;
  verifiedAt?: string;
  costEstimated?: boolean;
}

export interface Hotel {
  id: string;
  name: string;
  nightlyPrice: number;
  description: string;
  location: string;
  address?: string;
  mapQuery?: string;
  sourceUrl?: string;
  sourceName?: string;
  verifiedAt?: string;
  priceEstimated?: boolean;
}

export interface Catalog { places: Place[]; hotels: Hotel[] }
export interface TripStop { id: string; placeId: string; startTime: string; description?: string }
export interface TripDay { date: string; title: string; stops: TripStop[]; note: string }
export interface GeneratedSurface {
  title: string;
  html: string;
  css: string;
  javascript: string;
  placement: 'beforeStops' | 'afterStops' | 'replaceStops';
}
export interface TripChanges {
  hotelId?: string;
  budgetLimit?: number;
  nights?: number;
}
export interface RegionUI {
  id: RegionId;
  view: ViewType;
  binding: string;
  showCost: boolean;
  showAlternatives: boolean;
  compact: boolean;
  showTransport: boolean;
  showMap: boolean;
  transportMode: TransportMode;
  mapPlacement?: 'beforeStops' | 'afterStops';
  transportStyle?: 'inline' | 'summary';
  blocks: ContentBlock[];
  surface?: GeneratedSurface | null;
  surfaceState?: Record<string, unknown>;
}
export interface HistoryEntry {
  id: string;
  timestamp: string;
  summary: string;
  kind: string;
  version: number;
}
export interface TripSetupRequest {
  city: string;
  dayCount: number;
  budgetLimit: number;
  startDate: string;
  travelers: number;
  preferences: string;
}
export interface PlanningSettings extends TripSetupRequest {
  roomCount?: number;
  budgetBasis?: string;
  pricingNotice?: string;
  warnings?: string[];
}
export interface Session {
  tripDocument?: TripDocument;
  componentRegistry?: Record<string, ComponentBinding>;
  persistedConstraints?: PersistedConstraints;
  presentationSettings?: { showBudgetDetails: boolean };
  id: string;
  version: number;
  catalog?: Catalog;
  planningSettings?: PlanningSettings;
  planningWarnings?: string[];
  tripState: {
    city: string;
    travelers?: number;
    roomCount?: number;
    budgetLimit: number;
    hotelId: string;
    nights: number;
    days: Record<DayKey, TripDay>;
  };
  uiSpec: { regions: Record<RegionId, RegionUI> };
  controlState: { lockedRegions: string[]; hotelLocked: boolean };
  derived: { attractions: number; transport: number; food: number; hotel: number; total: number; remaining: number };
  history: HistoryEntry[];
  canUndo: boolean;
  engine: 'llm' | 'demo' | 'unconfigured';
  routeData?: Partial<Record<RegionId, RouteDay>>;
}
export interface Proposal {
  proposalId: string;
  baseVersion: number;
  targetRegionId: TargetRegion;
  nextUI: RegionUI;
  nextData?: TripDay;
  nextTrip?: TripChanges;
  summary: string;
  changes: string[];
  warnings: string[];
  engine: 'llm' | 'demo';
  affectedRegionIds?: RegionId[];
  updates?: { regionId: RegionId; nextUI: RegionUI; nextData?: TripDay; routeData?: RouteDay }[];
  title?: string;
  rationale?: string;
  changeCount?: number;
  preserved?: string[];
  trace?: AgentTrace[];
  message?: string;
}
export interface ProposalBatch extends Proposal {
  generateCandidates?: boolean | null;
  candidateCount?: number;
  minimumCandidates?: number;
  candidates?: Proposal[];
  scopeSummary?: string[];
  preservedSummary?: string[];
  candidateNotes?: string[];
}
export interface DirectUpdate {
  kind: 'note' | 'hotel' | 'budget' | 'replace_stop' | 'time' | 'surface_state';
  targetRegionId?: RegionId;
  value: string | number | Record<string, unknown>;
  stopId?: string;
}

export const DAY_ITEMS: { key: DayKey; region: RegionId; label: string; number: string }[] = [
  { key: 'day1', region: 'day-1', label: '第一天', number: '01' },
  { key: 'day2', region: 'day-2', label: '第二天', number: '02' },
  { key: 'day3', region: 'day-3', label: '第三天', number: '03' },
];

export interface DayItem { key: DayKey; region: RegionId; label: string; number: string }
export function sessionDayItems(session?: Session | null): DayItem[] {
  const digits = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十'];
  return Object.keys(session?.tripState.days ?? {}).filter(key => /^day\d+$/.test(key))
    .sort((a, b) => Number(a.slice(3)) - Number(b.slice(3)))
    .map(key => {
      const value = Number(key.slice(3));
      const name = value <= 10 ? digits[value] : value < 20 ? `十${digits[value - 10]}` : String(value);
      return { key: key as DayKey, region: `day-${value}` as RegionId, label: `第${name}天`, number: String(value).padStart(2, '0') };
    });
}
