export interface BuildInfo {
  build_id: string
  commit: string
  built_at: string
  assets?: string[]
  service?: 'api'
  version?: string
}

declare const __APP_BUILD_INFO__: BuildInfo

export const FRONTEND_BUILD_INFO = __APP_BUILD_INFO__

const UNKNOWN_BUILD_VALUES = new Set(['', 'unknown', 'unverified', 'development'])

export function isVerifiedBuild(info: BuildInfo): boolean {
  return !UNKNOWN_BUILD_VALUES.has(info.build_id.toLowerCase())
    && !UNKNOWN_BUILD_VALUES.has(info.commit.toLowerCase())
    && !UNKNOWN_BUILD_VALUES.has(info.built_at.toLowerCase())
}

export function buildsMatch(left: BuildInfo, right: BuildInfo): boolean {
  return left.build_id === right.build_id
    && left.commit === right.commit
    && left.built_at === right.built_at
}

export function formatBuildTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('ru-RU', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(date) + ' UTC'
}
