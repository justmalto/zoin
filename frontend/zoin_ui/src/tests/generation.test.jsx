// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import GenerationStatus from '../components/GenerationStatus'
import { generationError } from '../lib/generationError'
afterEach(()=>{cleanup();vi.useRealTimers()})
test('shows an honest estimate and elapsed time while generation is pending',()=>{
 vi.useFakeTimers();vi.setSystemTime(100000)
 render(<GenerationStatus pending startedAt={100000} />)
 expect(screen.getByRole('status').textContent).toContain('10–60 seconds')
 act(()=>vi.advanceTimersByTime(61000))
 expect(screen.getByRole('status').textContent).toContain('61s elapsed')
 expect(screen.getByRole('status').textContent).toContain('Taking longer than expected')
})
test('rate limit explains the failure and enables retry only after cooldown',()=>{
 vi.useFakeTimers();vi.setSystemTime(100000);const retry=vi.fn()
 render(<GenerationStatus error={generationError({status:429,message:'Groq rate limit'})} retryAt={103000} onRetry={retry} />)
 expect(screen.getByRole('alert').textContent).toContain('free-tier')
 expect(screen.getByRole('button').disabled).toBe(true)
 act(()=>vi.advanceTimersByTime(3000))
 fireEvent.click(screen.getByRole('button',{name:'Retry generation'}))
 expect(retry).toHaveBeenCalledOnce()
})
test('oversized inputs explain how to recover without pretending a score exists',()=>{
 expect(generationError({status:413,message:'Too large'}).explanation).toContain('Shorten non-essential text')
 expect(generationError({status:502,message:'Unknown evidence'}).explanation).toContain('unverified score will not be displayed')
})
