import { createClient } from '@supabase/supabase-js'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

// console.log("SUPABASE URL:", import.meta.env.VITE_SUPABASE_URL);
// console.log("SUPABASE KEY:", import.meta.env.VITE_SUPABASE_ANON_KEY);


export const supabase = createClient(supabaseUrl, supabaseAnonKey)

// console.log("Supabase client created:", supabase);