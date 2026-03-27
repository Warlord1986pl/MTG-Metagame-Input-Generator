$ErrorActionPreference = 'Stop'

function Get-MtgoJson([string]$url) {
    $html = (Invoke-WebRequest -Uri $url -TimeoutSec 60).Content
    $m = [regex]::Match(
        $html,
        'window\.MTGO\.decklists\.data\s*=\s*(\[.*?\])\s*;\s*window\.MTGO\.decklists\.type',
        [System.Text.RegularExpressions.RegexOptions]::Singleline
    )
    if (-not $m.Success) {
        return $null
    }

    $json = $m.Groups[1].Value
    try {
        return ($json | ConvertFrom-Json)
    }
    catch {
        return $null
    }
}

$rangeStart = [datetime]'2026-03-02'
$rangeEnd = [datetime]'2026-03-15'
$listHtml = (Invoke-WebRequest -Uri 'https://www.mtgo.com/decklists' -TimeoutSec 60).Content

$pattern = '<a href="(?<href>/decklist/[^"]+)" class="decklists-link">[\s\S]*?<h3>(?<title>[^<]+)</h3>[\s\S]*?<time datetime="(?<dt>[^"]+)"'
$matches = [regex]::Matches($listHtml, $pattern, [System.Text.RegularExpressions.RegexOptions]::Singleline)

$events = @()
foreach ($mm in $matches) {
    $href = $mm.Groups['href'].Value
    $title = $mm.Groups['title'].Value.Trim()
    $dt = [datetime]$mm.Groups['dt'].Value

    if ($dt.Date -lt $rangeStart.Date -or $dt.Date -gt $rangeEnd.Date) {
        continue
    }
    if ($title -notmatch 'Modern') {
        continue
    }

    $events += [pscustomobject]@{
        href = $href
        title = $title
        dt = $dt
    }
}

$events = $events | Sort-Object -Property href -Unique | Sort-Object -Property dt -Descending
Write-Output "EVENTS_IN_RANGE=$($events.Count)"

$totalDeckRows = 0
$decksWithWL = 0
$totalWins = 0
$totalLosses = 0
$totalBracketMatches = 0
$sampleTitles = @()

foreach ($e in $events) {
    $url = 'https://www.mtgo.com' + $e.href
    $data = Get-MtgoJson $url
    if ($null -eq $data) {
        Write-Output "NO_DATA $url"
        continue
    }

    if ($sampleTitles.Count -lt 8) {
        $sampleTitles += $e.title
    }

    foreach ($row in $data) {
        if ($null -ne $row.main_deck) {
            $totalDeckRows += 1
        }

        if ($null -ne $row.wins -and $null -ne $row.wins.wins -and $null -ne $row.wins.losses) {
            $decksWithWL += 1
            $totalWins += [int]$row.wins.wins
            $totalLosses += [int]$row.wins.losses
        }
    }

    if ($null -ne $data.rounds) {
        foreach ($round in $data.rounds) {
            if ($null -ne $round.matches) {
                $totalBracketMatches += $round.matches.Count
            }
        }
    }
}

$overallWr = 'n/a'
if (($totalWins + $totalLosses) -gt 0) {
    $overallWr = [math]::Round(($totalWins / ($totalWins + $totalLosses)) * 100, 2)
}

Write-Output "TOTAL_DECK_ROWS=$totalDeckRows"
Write-Output "DECKS_WITH_WL=$decksWithWL"
Write-Output "SUM_WINS=$totalWins"
Write-Output "SUM_LOSSES=$totalLosses"
Write-Output "OVERALL_WR_PERCENT=$overallWr"
Write-Output "BRACKET_MATCH_ROWS=$totalBracketMatches"
Write-Output "SAMPLE_EVENTS="
$sampleTitles | ForEach-Object { Write-Output " - $_" }
