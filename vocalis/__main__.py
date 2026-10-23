import sys

if __name__ == "__main__":
    if "--parametres" not in sys.argv:
        # Nouvelle version ? On installe d'abord les bibliothèques qui ont changé.
        from vocalis.updater import ensure_up_to_date
        ensure_up_to_date()
    from vocalis.main import main
    main()
