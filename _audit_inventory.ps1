$root = 'd:\devine\lms'
Get-ChildItem -Path $root -Recurse -File |
  Where-Object { $_.FullName -notmatch '\\.git\\|__pycache__|node_modules|\\.venv\\' } |
  ForEach-Object {
    $lines = 0
    if ($_.Length -lt 2MB) {
      try { $lines = (Get-Content -LiteralPath $_.FullName -ErrorAction Stop | Measure-Object -Line).Lines } catch { $lines = -1 }
    }
    '{0}`t{1}`t{2}' -f $lines, $_.Length, $_.FullName
  }