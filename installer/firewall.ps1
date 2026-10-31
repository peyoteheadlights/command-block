# Windows Firewall rules for the Command Block agent.
# Run in an Administrator PowerShell:  .\installer\firewall.ps1
#
# This does NOT disable the firewall. It creates one narrow inbound rule that
# allows the agent port only from the Tailscale CGNAT range (100.64.0.0/10),
# which is the only range your tailnet peers can come from. Anything arriving
# from your LAN or from the internet is still blocked by the default policy.

#
# With -ServerPorts "TCP:25565,UDP:19132" it does the other job instead: one
# inbound rule per Minecraft server port, and the broad "allow Java on every
# port" rules Windows made when Java first asked are turned off (not deleted,
# so -Remove turns them back on).

param(
    [int]$Port = 8765,
    [int]$RedirectPort = 8080,
    [string]$ServerPorts = "",
    [switch]$Remove
)

$RuleName  = "Command Block (HTTPS, Tailscale only)"
$RedirName = "Command Block (HTTP redirect, Tailscale only)"
$GameRule  = "Command Block game port"
$JavaNote  = "Turned off by Command Block, which uses one rule per server port instead."

function Remove-GamePorts {
    Get-NetFirewallRule -DisplayName "$GameRule *" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
}

if ($Remove) {
    Remove-NetFirewallRule -DisplayName $RuleName  -ErrorAction SilentlyContinue
    Remove-NetFirewallRule -DisplayName $RedirName -ErrorAction SilentlyContinue
    Remove-GamePorts
    # Put back the Java rules this script turned off.
    Get-NetFirewallRule -Direction Inbound | Where-Object { $_.Description -eq $JavaNote } |
        Set-NetFirewallRule -Enabled True -Description ""
    Write-Host "Removed the Command Block firewall rules."
    exit 0
}

if ($ServerPorts) {
    Remove-GamePorts
    foreach ($item in $ServerPorts.Split(",")) {
        $protocol, $number = $item.Split(":")
        if ($protocol -notin @("TCP", "UDP") -or $number -notmatch '^[0-9]{1,5}$') {
            Write-Error "Not a port: $item"
            exit 2
        }
        New-NetFirewallRule `
            -DisplayName "$GameRule $protocol $number" `
            -Description "Lets players reach one Minecraft server, on this port only." `
            -Direction Inbound -Action Allow -Protocol $protocol -LocalPort ([int]$number) `
            -Profile Any | Out-Null
        Write-Host "Allowed $protocol $number"
    }
    # The rules Windows made for java.exe or javaw.exe on every port.
    $java = Get-NetFirewallApplicationFilter |
        Where-Object { $_.Program -match '\\javaw?\.exe$' } |
        Get-NetFirewallRule |
        Where-Object { $_.Direction -eq "Inbound" -and $_.Action -eq "Allow" -and $_.Enabled -eq "True" }
    foreach ($rule in $java) {
        Set-NetFirewallRule -Name $rule.Name -Enabled False -Description $JavaNote
        Write-Host "Turned off: $($rule.DisplayName)"
    }
    exit 0
}

Remove-NetFirewallRule -DisplayName $RuleName  -ErrorAction SilentlyContinue
Remove-NetFirewallRule -DisplayName $RedirName -ErrorAction SilentlyContinue
# The rules' names from before the rename to Command Block.
Remove-NetFirewallRule -DisplayName "Minecraft Server Control (HTTPS, Tailscale only)" -ErrorAction SilentlyContinue
Remove-NetFirewallRule -DisplayName "Minecraft Server Control (HTTP redirect, Tailscale only)" -ErrorAction SilentlyContinue

New-NetFirewallRule `
    -DisplayName $RuleName `
    -Description "Allows the Command Block agent over HTTPS from Tailscale peers only." `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
    -RemoteAddress 100.64.0.0/10 `
    -Profile Any | Out-Null

New-NetFirewallRule `
    -DisplayName $RedirName `
    -Description "Allows the HTTP listener that only redirects to HTTPS, from Tailscale peers only." `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $RedirectPort `
    -RemoteAddress 100.64.0.0/10 `
    -Profile Any | Out-Null

Write-Host ""
Write-Host "Created two inbound rules:"
Write-Host "  $RuleName"
Write-Host "    TCP $Port, remote address 100.64.0.0/10 (Tailscale only)"
Write-Host "  $RedirName"
Write-Host "    TCP $RedirectPort, remote address 100.64.0.0/10 (Tailscale only)"
Write-Host ""
Write-Host "Nothing was opened to your LAN or to the internet, and the firewall"
Write-Host "itself remains enabled. Verify with:"
Write-Host "  Get-NetFirewallRule -DisplayName '$RuleName' | Get-NetFirewallAddressFilter"
Write-Host ""
Write-Host "To remove the rules later:  .\installer\firewall.ps1 -Remove"
