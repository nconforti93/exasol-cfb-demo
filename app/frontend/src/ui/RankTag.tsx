import { formatPollRank } from '../format/format.ts'

export interface RankTagProps {
  rank: number | null
  /** The poll, for assistive technology: "AP rank 6". */
  poll?: 'AP' | 'CFP'
}

/** A poll rank as "#6"; nothing for an unranked team. */
export function RankTag({ rank, poll }: RankTagProps) {
  if (rank === null) return null
  return (
    <span className="rank-tag num" {...(poll === undefined ? {} : { role: 'img', 'aria-label': `${poll} rank ${rank}` })}>
      {formatPollRank(rank)}
    </span>
  )
}
