export const passwordRules = [
  ['At least 12 characters', value => value.length >= 12],
  ['An uppercase letter', value => /[A-Z]/.test(value)],
  ['A lowercase letter', value => /[a-z]/.test(value)],
  ['A number', value => /[0-9]/.test(value)],
  ['A symbol', value => /[!@#$%^&*()_+\-=[\]{};'":\\|<>?,./`~]/.test(value)],
]
export const validPassword = value => passwordRules.every(([, check]) => check(value))
