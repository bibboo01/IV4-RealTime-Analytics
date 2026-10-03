param([string]$Nssm = "nssm.exe", [string]$ServiceName = "IV4DataAgent")
& $Nssm stop $ServiceName
& $Nssm remove $ServiceName confirm
