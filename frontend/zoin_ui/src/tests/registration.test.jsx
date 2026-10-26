// @vitest-environment jsdom
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import Register from '../pages/register'
import CheckEmail from '../pages/checkEmail'
import { validPassword } from '../lib/passwordPolicy'
const { signUp } = vi.hoisted(() => ({ signUp: vi.fn() }))
vi.mock('../lib/supabaseClient', () => ({ supabase: { auth: { signUp } } }))
afterEach(cleanup)
beforeEach(() => signUp.mockReset())
function setup() {
  render(<MemoryRouter initialEntries={['/register']}><Routes>
    <Route path="/register" element={<Register />} /><Route path="/check-email" element={<CheckEmail />} />
    <Route path="/login" element={<h1>Login destination</h1>} /><Route path="/home" element={<h1>Home destination</h1>} />
  </Routes></MemoryRouter>)
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Synthetic Test' } })
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'synthetic@example.invalid' } })
}
function passwords(value, confirmation=value) {
  fireEvent.change(screen.getByLabelText('Password', { exact: true }), { target: { value } })
  fireEvent.change(screen.getByLabelText('Confirm Password'), { target: { value:confirmation } })
  fireEvent.submit(screen.getByRole('button', { name:'Sign Up' }).closest('form'))
}
test('rejects weak passwords even if native validation is bypassed', () => {
  setup(); passwords('abcdefghijklmnop')
  expect(signUp).not.toHaveBeenCalled()
  expect(screen.getByRole('alert').textContent).toContain('12 characters')
})
test('rejects mismatched confirmation without calling signup', () => {
  setup(); passwords('Synthetic-Test26!', 'Different-Test26!')
  expect(signUp).not.toHaveBeenCalled(); expect(screen.getByRole('alert').textContent).toContain('do not match')
})
test('successful confirmed-email signup navigates to instructions then login', async () => {
  signUp.mockResolvedValue({ data:{session:null}, error:null }); setup(); passwords('Synthetic-Test26!')
  await screen.findByRole('heading',{name:'Check your email'})
  expect(screen.getByText('synthetic@example.invalid')).toBeTruthy()
  await waitFor(() => expect(document.activeElement.textContent).toBe('Check your email'))
  fireEvent.click(screen.getByRole('link',{name:'Go to Login'}))
  await screen.findByRole('heading',{name:'Login destination'})
})
test('signup failure preserves fields and remains on registration', async () => {
  signUp.mockResolvedValue({data:null,error:new Error('Email delivery unavailable')}); setup(); passwords('Synthetic-Test26!')
  await waitFor(()=>expect(screen.getByRole('alert').textContent).toContain('Email delivery unavailable'))
  expect(screen.getByLabelText('Email').value).toBe('synthetic@example.invalid')
})
test('already-issued session goes to home rather than misleading email instructions', async () => {
  signUp.mockResolvedValue({data:{session:{user:{id:'fixture'}}},error:null});setup();passwords('Synthetic-Test26!')
  await screen.findByRole('heading',{name:'Home destination'})
})
test('password policy rejects missing classes and accepts supported symbols', () => {
  for (const value of ['Aa1!short','lowercase12345!','UPPERCASE12345!','NoNumbersHere!!','NoSymbolsHere123']) expect(validPassword(value)).toBe(false)
  expect(validPassword('Synthetic[Test26]')).toBe(true)
})
