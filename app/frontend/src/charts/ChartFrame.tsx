import { useId, useState, type ReactNode } from 'react'

export interface TableView {
  columns: string[]
  rows: Array<Array<string | number>>
}

interface ChartFrameProps {
  title: string
  table: TableView
  note?: string
  children: ReactNode
}

/** A titled figure whose chart can be swapped for the same numbers as a table. */
export function ChartFrame({ title, table, note, children }: ChartFrameProps) {
  const captionId = useId()
  const [showTable, setShowTable] = useState(false)
  return (
    <figure className="chart-frame" aria-labelledby={captionId}>
      <figcaption id={captionId} className="chart-title">
        {title}
      </figcaption>
      <button type="button" className="chart-toggle" aria-pressed={showTable} onClick={() => setShowTable(!showTable)}>
        {showTable ? 'Chart view' : 'Table view'}
      </button>
      {showTable ? (
        <table className="chart-table">
          <thead>
            <tr>
              {table.columns.map((column) => (
                <th key={column} scope="col">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, index) => (
              <tr key={index}>
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex} className="num">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        children
      )}
      {note === undefined ? null : <p className="chart-note">{note}</p>}
    </figure>
  )
}
