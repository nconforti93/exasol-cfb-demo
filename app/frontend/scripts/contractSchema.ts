import ts from 'typescript'

type Schema = Record<string, unknown>

const NULL_SCHEMA: Schema = { type: 'null' }

function literalOf(node: ts.LiteralTypeNode): Schema {
  const literal = node.literal
  if (literal.kind === ts.SyntaxKind.NullKeyword) return NULL_SCHEMA
  if (ts.isStringLiteral(literal)) return { const: literal.text }
  if (ts.isNumericLiteral(literal)) return { const: Number(literal.text) }
  throw new Error(`unsupported literal: ${literal.getText()}`)
}

function unionOf(members: Schema[]): Schema {
  const consts = members.filter((m) => 'const' in m && Object.keys(m).length === 1)
  if (consts.length === members.length) return { enum: consts.map((m) => m.const) }
  return { anyOf: members }
}

function convert(node: ts.TypeNode): Schema {
  switch (node.kind) {
    case ts.SyntaxKind.StringKeyword:
      return { type: 'string' }
    case ts.SyntaxKind.NumberKeyword:
      return { type: 'number' }
    case ts.SyntaxKind.BooleanKeyword:
      return { type: 'boolean' }
    case ts.SyntaxKind.UnknownKeyword:
      return {}
  }
  if (ts.isLiteralTypeNode(node)) return literalOf(node)
  if (ts.isParenthesizedTypeNode(node)) return convert(node.type)
  if (ts.isArrayTypeNode(node)) return { type: 'array', items: convert(node.elementType) }
  if (ts.isUnionTypeNode(node)) return unionOf(node.types.map(convert))
  if (ts.isTypeLiteralNode(node)) return objectOf(node.members)
  if (ts.isTypeReferenceNode(node)) {
    const name = node.typeName.getText()
    if (name === 'Record' && node.typeArguments?.length === 2) {
      return { type: 'object', additionalProperties: convert(node.typeArguments[1] as ts.TypeNode) }
    }
    return { $ref: `#/$defs/${name}` }
  }
  throw new Error(`unsupported type node: ${node.getText()}`)
}

function objectOf(members: ts.NodeArray<ts.TypeElement>): Schema {
  const properties: Record<string, Schema> = {}
  const required: string[] = []
  for (const member of members) {
    if (!ts.isPropertySignature(member) || member.type === undefined) {
      throw new Error(`unsupported member: ${member.getText()}`)
    }
    const name = member.name.getText()
    properties[name] = convert(member.type)
    if (member.questionToken === undefined) required.push(name)
  }
  return { type: 'object', properties, required, additionalProperties: false }
}

/** JSON Schema (2020-12) of every exported interface and type alias in the given source. */
export function buildSchema(source: string): Schema {
  const file = ts.createSourceFile('types.ts', source, ts.ScriptTarget.ES2022, true)
  const defs: Record<string, Schema> = {}
  for (const statement of file.statements) {
    if (ts.isInterfaceDeclaration(statement)) {
      defs[statement.name.text] = objectOf(statement.members)
    } else if (ts.isTypeAliasDeclaration(statement)) {
      defs[statement.name.text] = convert(statement.type)
    }
  }
  return {
    $schema: 'https://json-schema.org/draft/2020-12/schema',
    title: 'CFB app API contract (generated from src/api/types.ts by `npm run schema`)',
    $defs: defs,
  }
}

export function renderSchema(source: string): string {
  return `${JSON.stringify(buildSchema(source), null, 2)}\n`
}
