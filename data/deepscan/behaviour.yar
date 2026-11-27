// Command Block behaviour check. Each rule looks inside one Java class
// file for code that normal mods and plugins rarely have. A match is
// "Worth a look", never proof of malware.
//
// Each rule's "words" meta is the strings.json key the apps show.

rule loads_code_from_the_internet
{
    meta:
        words = "deepscan.rule.loads_code"
    strings:
        $loader = "java/net/URLClassLoader"
        $http = /https?:\/\/[a-zA-Z0-9.\-]{4,}/
        $find = "loadClass"
        $call = "java/lang/reflect/Method"
    condition:
        uint32be(0) == 0xCAFEBABE and $loader and $http and $find and $call
}

rule starts_a_program
{
    meta:
        words = "deepscan.rule.starts_program"
    strings:
        $runtime = "java/lang/Runtime"
        $exec = "exec"
        $builder = "java/lang/ProcessBuilder"
        $powershell = /-enc(odedcommand)? |-w(indowstyle)? hidden|Invoke-WebRequest|Invoke-Expression|DownloadString|DownloadFile|Start-Process|Add-MpPreference/ nocase
        $tools = /\b(curl|wget|certutil|bitsadmin|mshta|regsvr32|schtasks|vssadmin)\b/ nocase
        $hide = /attrib \+h|reg add/ nocase
    condition:
        uint32be(0) == 0xCAFEBABE
        and (($runtime and $exec) or $builder)
        and any of ($powershell, $tools, $hide)
}

rule reads_login_files
{
    meta:
        words = "deepscan.rule.login_files"
    strings:
        $leveldb = /Local Storage.{1,2}leveldb/
        $discord = /[\\\/](discord|discordcanary|discordptb|Lightcord)[\\\/]/ nocase
        $login_data = "Login Data"
        $cookies = /(Network.{1,2})?Cookies\b/
        $browser = /(Google.{1,2}Chrome|BraveSoftware|Microsoft.{1,2}Edge|Opera Software|Mozilla.{1,2}Firefox)/
        $launcher = /launcher_(accounts|profiles|accounts_microsoft_store)\.json/
        $lunar = /\.lunarclient|lunarclient.{1,2}settings/ nocase
        $feather = "feather/accounts" nocase
        $essential = /essential.{1,2}microsoft_accounts\.json/ nocase
        $tlauncher = "TlauncherProfiles.json" nocase
    condition:
        uint32be(0) == 0xCAFEBABE
        and ($leveldb or ($login_data and $browser) or ($cookies and $browser)
             or $launcher or $lunar or $feather or $essential or $tlauncher
             or ($discord and ($leveldb or $login_data)))
}

rule sends_to_a_discord_webhook
{
    meta:
        words = "deepscan.rule.webhook"
    strings:
        $hook = /discord(app)?\.com\/api\/webhooks\/[0-9]{17,20}\/[A-Za-z0-9_\-]{30,}/
    condition:
        uint32be(0) == 0xCAFEBABE and $hook
}

rule hides_encoded_code
{
    meta:
        words = "deepscan.rule.hidden_code"
    strings:
        // Base64 of the first bytes of a Java class, a jar or zip, and a Windows program.
        $class = "yv66vg"
        $zip = "UEsDBBQ"
        $exe = "TVqQAAMAAAAEAAAA"
    condition:
        uint32be(0) == 0xCAFEBABE and any of them
}
