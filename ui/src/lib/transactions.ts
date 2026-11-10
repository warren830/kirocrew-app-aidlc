import { TRANSACTION_SETTLED_STATUS } from './enums.generated'
import type { TransactionResult } from './types'

const SETTLED: ReadonlySet<string> = new Set(TRANSACTION_SETTLED_STATUS)

/** Has a transaction in this status stopped? The backend's own set (`TRANSACTION_SETTLED_STATUS`, generated). */
export function transactionSettled(status: string): boolean {
  return SETTLED.has(status)
}

/** The newest of these transactions (a repository's detail lists them newest first) that has not settled yet. */
export function unsettledTransaction(transactions: readonly TransactionResult[]): TransactionResult | null {
  return transactions.find((tx) => !transactionSettled(tx.status)) ?? null
}
